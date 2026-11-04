import json
import shutil
from pathlib import Path

import pytest

from ase.core.config import Settings
from ase.core.errors import (
    InvalidTransitionError,
    LLMResponseError,
    PolicyViolationError,
    RepositoryImportError,
    RunNotFoundError,
    UnsafeUrlError,
)
from ase.core.states import RunState
from ase.repository.importer import ImportLimits, RepositoryImporter
from ase.services.project_service import (
    EXECUTION_DISABLED_NOTE,
    NEXT_STEPS_NOTE,
    STEP6_NOTE,
    NO_PROVIDER_NOTE,
    NOTHING_TO_PLAN_NOTE,
    UNTRUSTED_NOTE,
    ProjectService,
)
from ase.storage.run_store import JsonRunStore
from ase.ui.presenters import usage_rows

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"


def _copy_fixture(url, dest, timeout):
    shutil.copytree(FIXTURE, dest)


def _service(tmp_path, clone_fn=_copy_fixture, provider=None, secondary=None, sandbox=None):
    settings = Settings(_env_file=None, workspace_root=tmp_path)
    importer = RepositoryImporter(tmp_path, ImportLimits.from_settings(settings), clone_fn)
    return ProjectService(
        settings, JsonRunStore(tmp_path), importer, provider, secondary, sandbox
    )


def test_environment_is_honest_by_default(tmp_path):
    env = _service(tmp_path).environment_status()
    assert env.llm_configured is False and env.llm_model is None
    assert env.code_execution_available is False


def test_successful_import_reaches_imported_with_inventory(tmp_path):
    svc = _service(tmp_path)
    run = svc.start_import("https://github.com/octocat/Hello-World")
    assert run.state is RunState.IMPORTED
    assert run.inventory is not None and run.inventory.ecosystems == ["python"]
    assert UNTRUSTED_NOTE in run.limitations
    assert [a.name for a in run.actions] == ["validate_repository_url", "import_repository"]
    assert svc.get_run(run.run_id) == run  # persisted


def test_failed_import_is_recorded_as_failed_not_hidden(tmp_path):
    def broken(url, dest, timeout):
        raise RepositoryImportError("git clone failed (exit 128): not found")

    svc = _service(tmp_path, clone_fn=broken)
    run = svc.start_import("https://github.com/a/b")
    assert run.state is RunState.FAILED and run.inventory is None
    assert run.actions[-1].exit_status == "error"
    assert "not found" in run.history[-1].reason


def test_secrets_in_clone_errors_are_redacted(tmp_path):
    def leaky(url, dest, timeout):
        raise RepositoryImportError("auth failed token ghp_" + "a" * 36)

    run = _service(tmp_path, clone_fn=leaky).start_import("https://github.com/a/b")
    assert "ghp_" not in run.history[-1].reason and "ghp_" not in run.actions[-1].summary


def test_bad_url_creates_no_run(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(UnsafeUrlError):
        svc.start_import("https://evil.com/a/b")
    assert svc.list_runs() == []


def test_cancel_run_then_cancel_again_fails(tmp_path):
    svc = _service(tmp_path)
    run = svc.start_import("https://github.com/a/b")
    assert svc.cancel_run(run.run_id).state is RunState.CANCELLED
    with pytest.raises(InvalidTransitionError):
        svc.cancel_run(run.run_id)


def test_get_unknown_run_raises(tmp_path):
    with pytest.raises(RunNotFoundError):
        _service(tmp_path).get_run("0" * 32)


_GOOD_REPLY = (
    '{"summary": "s", "findings": [{"severity": "high", "category": "bug", '
    '"description": "d", "evidence": [{"file_path": "cart.py", "line_start": 6, '
    '"line_end": 6}], "recommended_fix": "f", "confidence": 0.8}]}'
)


_GOOD_PLAN = json.dumps({
    "architecture_summary": "A tiny cart module.",
    "changes": [{"action": "modify", "path": "cart.py", "summary": "Subtract the discount",
                 "finding_ids": ["F-001"], "verification": "tests/test_cart.py", "risk": "low"}],
    "test_strategy": "Run the existing tests.",
})  # fmt: skip
_GOOD_EDIT = json.dumps({
    "edits": [{
        "old": "    return price + price * percent / 100\n",
        "new": "    return price - price * percent / 100\n",
    }]
})
PASS_ALL = {"compile": (0, ""), "install": (0, ""), "install-test-runner": (0, ""),
            "tests": (0, "2 passed")}  # fmt: skip
FAIL_TESTS = {**PASS_ALL, "tests": (1, "FAILED test_discount - assert 110.0 == 90.0")}
_REPAIR = json.dumps({"files": [{"path": "cart.py", "edits": [{
    "old": "    return price - price * percent / 100\n",
    "new": "    return price * (1 - percent / 100)\n"}]}]})  # fmt: skip
_NO_FINDINGS = '{"summary": "Nothing wrong found.", "findings": []}'


def test_audit_moves_run_to_analyzed(tmp_path, scripted_provider):
    svc = _service(tmp_path, provider=scripted_provider([_GOOD_REPLY]))
    run = svc.start_import("https://github.com/a/b")
    run = svc.run_audit(run.run_id)
    assert run.state is RunState.ANALYZED
    assert run.audit is not None and len(run.audit.findings) == 1
    assert "1 finding(s) kept" in run.actions[-1].summary
    assert svc.get_run(run.run_id).audit == run.audit  # persisted


def test_failed_audit_keeps_run_imported_and_records_the_error(tmp_path, scripted_provider):
    svc = _service(tmp_path, provider=scripted_provider(["bad", "worse"]))
    run = svc.start_import("https://github.com/a/b")
    with pytest.raises(LLMResponseError):
        svc.run_audit(run.run_id)
    stored = svc.get_run(run.run_id)
    assert stored.state is RunState.IMPORTED and stored.audit is None
    assert stored.actions[-1].name == "ai_audit" and stored.actions[-1].exit_status == "error"


def test_cannot_audit_twice_or_before_import(tmp_path, scripted_provider):
    svc = _service(tmp_path, provider=scripted_provider([_GOOD_REPLY]))
    run = svc.run_audit(svc.start_import("https://github.com/a/b").run_id)
    with pytest.raises(PolicyViolationError):
        svc.run_audit(run.run_id)

    def broken(url, dest, timeout):
        raise RepositoryImportError("x")

    failed = _service(tmp_path / "other", clone_fn=broken).start_import("https://github.com/a/b")
    assert failed.state is RunState.FAILED


def test_audit_without_a_provider_is_refused_not_faked(tmp_path):
    svc = _service(tmp_path)
    run = svc.start_import("https://github.com/a/b")
    with pytest.raises(PolicyViolationError):
        svc.run_audit(run.run_id)
    assert svc.get_run(run.run_id).audit is None


def _collect():
    events = []
    return events, lambda stage, status: events.append((stage, status))


def test_pipeline_stops_after_the_build_when_execution_is_disabled(tmp_path, scripted_provider):
    svc = _service(tmp_path, provider=scripted_provider([_GOOD_REPLY, _GOOD_PLAN, _GOOD_EDIT]))
    events, cb = _collect()
    run = svc.run_pipeline("https://github.com/a/b", cb)
    assert run.state is RunState.BUILT and run.plan is not None and run.build is not None
    assert (run.build.applied, run.build.failed) == (1, 0)
    assert events == [
        ("import", "running"), ("import", "done"), ("audit", "running"), ("audit", "done"),
        ("plan", "running"), ("plan", "done"), ("build", "running"),
        ("build", "C-001: modify cart.py"), ("build", "done"), ("verify", "skipped"),
    ]  # fmt: skip
    # the approval and execution are on the record, in order
    assert [c.to_state for c in run.history][-3:] == [
        RunState.AWAITING_APPROVAL, RunState.EXECUTING, RunState.BUILT]  # fmt: skip
    assert run.approved_scope == ["cart.py"]
    assert EXECUTION_DISABLED_NOTE in run.limitations and NEXT_STEPS_NOTE in run.limitations
    assert run.verification is None  # nothing was run, so nothing is claimed
    # the build copy changed; the untouched clone did not
    assert "price - price" in (tmp_path / "build" / run.run_id / "cart.py").read_text()
    assert "price + price" in (tmp_path / "repos" / run.run_id / "cart.py").read_text()
    assert svc.get_run(run.run_id).build == run.build  # persisted


def test_build_that_applies_nothing_ends_failed_with_reasons(tmp_path, scripted_provider):
    svc = _service(tmp_path, provider=scripted_provider([_GOOD_REPLY, _GOOD_PLAN, "bad", "worse"]))
    events, cb = _collect()
    run = svc.run_pipeline("https://github.com/a/b", cb)
    assert run.state is RunState.FAILED and run.build is not None
    assert (run.build.applied, run.build.failed) == (0, 1) and run.build.changes[0].reason
    assert run.actions[-1].name == "build" and run.actions[-1].exit_status == "error"
    assert events[-1] == ("build", "failed")
    assert "price + price" in (tmp_path / "repos" / run.run_id / "cart.py").read_text()


def test_building_requires_a_plan_and_a_provider(tmp_path, scripted_provider):
    svc = _service(tmp_path, provider=scripted_provider([_GOOD_REPLY]))
    run = svc.start_import("https://github.com/a/b")
    with pytest.raises(PolicyViolationError):  # no plan yet
        svc.run_build(run.run_id)
    with pytest.raises(PolicyViolationError):  # no provider configured
        _service(tmp_path / "other").run_build(run.run_id)


def test_pipeline_without_provider_imports_then_says_audit_was_skipped(tmp_path):
    svc = _service(tmp_path)
    events, cb = _collect()
    run = svc.run_pipeline("https://github.com/a/b", cb)
    assert run.state is RunState.IMPORTED and run.audit is None
    assert NO_PROVIDER_NOTE in run.limitations
    assert events[-1] == ("audit", "skipped")


def test_pipeline_stops_when_import_fails_and_never_calls_the_model(tmp_path, scripted_provider):
    def broken(url, dest, timeout):
        raise RepositoryImportError("clone failed")

    provider = scripted_provider([_GOOD_REPLY])
    svc = _service(tmp_path, clone_fn=broken, provider=provider)
    events, cb = _collect()
    run = svc.run_pipeline("https://github.com/a/b", cb)
    assert run.state is RunState.FAILED and provider.requests == []
    assert events == [("import", "running"), ("import", "failed")]


def test_pipeline_skips_planning_when_the_audit_found_nothing(tmp_path, scripted_provider):
    svc = _service(tmp_path, provider=scripted_provider([_NO_FINDINGS]))
    events, cb = _collect()
    run = svc.run_pipeline("https://github.com/a/b", cb)
    assert run.state is RunState.ANALYZED and run.plan is None
    assert NOTHING_TO_PLAN_NOTE in run.limitations
    assert events[-2:] == [("audit", "done"), ("plan", "skipped")]


def test_failed_plan_keeps_the_audit_and_is_reported_not_hidden(tmp_path, scripted_provider):
    svc = _service(tmp_path, provider=scripted_provider([_GOOD_REPLY, "bad", "worse"]))
    events, cb = _collect()
    run = svc.run_pipeline("https://github.com/a/b", cb)
    assert run.state is RunState.ANALYZED and run.audit is not None and run.plan is None
    assert run.actions[-1].name == "architecture_plan" and run.actions[-1].exit_status == "error"
    assert events[-1] == ("plan", "failed")


def test_a_plan_with_no_valid_changes_is_an_error_not_a_success(tmp_path, scripted_provider):
    bad = json.dumps({"changes": [{"action": "delete", "path": "cart.py", "summary": "x",
                                   "finding_ids": ["F-001"]}]})  # fmt: skip
    svc = _service(tmp_path, provider=scripted_provider([_GOOD_REPLY, bad]))
    events, cb = _collect()
    run = svc.run_pipeline("https://github.com/a/b", cb)
    assert run.state is RunState.ANALYZED and run.plan is None
    assert "no valid changes" in run.actions[-1].summary and events[-1] == ("plan", "failed")


def test_planning_requires_an_audit_and_a_provider(tmp_path, scripted_provider):
    svc = _service(tmp_path, provider=scripted_provider([_GOOD_REPLY]))
    run = svc.start_import("https://github.com/a/b")
    with pytest.raises(PolicyViolationError):  # not audited yet
        svc.run_plan(run.run_id)
    with pytest.raises(PolicyViolationError):  # no provider configured
        _service(tmp_path / "other").run_plan(run.run_id)


def test_pipeline_reports_audit_failure_without_claiming_success(tmp_path, scripted_provider):
    svc = _service(tmp_path, provider=scripted_provider(["bad", "worse"]))
    events, cb = _collect()
    run = svc.run_pipeline("https://github.com/a/b", cb)
    assert run.state is RunState.IMPORTED and run.audit is None
    assert run.actions[-1].exit_status == "error"
    assert events[-1] == ("audit", "failed")


def test_steps_can_be_routed_to_a_second_provider_and_the_record_shows_it(
    tmp_path, scripted_provider
):
    main = scripted_provider([_GOOD_REPLY, _GOOD_PLAN])
    main.name = "baseten"
    second = scripted_provider([_GOOD_EDIT])
    second.name = "modal"
    svc = _service(tmp_path, provider=main, secondary=second)  # default routing: build -> second
    assert [(r.stage, r.provider) for r in svc.environment_status().routes] == [
        ("audit", "baseten"), ("plan", "baseten"), ("build", "modal")]  # fmt: skip
    run = svc.run_pipeline("https://github.com/a/b")
    assert run.state is RunState.BUILT
    assert (run.audit.provider, run.plan.provider, run.build.provider) == (
        "baseten", "baseten", "modal")  # fmt: skip
    assert len(main.requests) == 2 and len(second.requests) == 1
    assert [r["Provider"] for r in usage_rows(run)[:3]] == ["baseten", "baseten", "modal"]


def test_the_environment_is_not_fully_configured_without_a_provider_for_every_step(tmp_path):
    svc = _service(tmp_path)
    assert svc.environment_status().llm_configured is False
    assert svc.environment_status().routes == []


def _full(tmp_path, scripted_provider, scripted_sandbox, sandbox_script, extra_replies=()):
    provider = scripted_provider([_GOOD_REPLY, _GOOD_PLAN, _GOOD_EDIT, *extra_replies])
    sandbox = scripted_sandbox(sandbox_script)
    return _service(tmp_path, provider=provider, sandbox=sandbox), provider, sandbox


def test_verified_run_ends_completed_verified_with_the_full_trail(
    tmp_path, scripted_provider, scripted_sandbox
):
    svc, _, sandbox = _full(tmp_path, scripted_provider, scripted_sandbox, [PASS_ALL])
    assert svc.environment_status().code_execution_available is True
    events, cb = _collect()
    run = svc.run_pipeline("https://github.com/a/b", cb)
    assert run.state is RunState.COMPLETED and run.final_status == "completed_verified"
    assert run.verification.outcome == "passed" and STEP6_NOTE in run.limitations
    states = [c.to_state for c in run.history]
    assert states[-4:] == [RunState.EXECUTING, RunState.BUILT, RunState.VERIFYING,
                           RunState.COMPLETED]  # fmt: skip
    assert events[-7:] == [
        ("verify", "running"), ("verify", "round 1: running checks in the sandbox"),
        ("verify", "python: compile"), ("verify", "python: install"),
        ("verify", "python: install-test-runner"), ("verify", "python: tests"),
        ("verify", "done"),
    ]  # fmt: skip
    assert sandbox.opened == ["python"]
    assert svc.get_run(run.run_id).verification == run.verification  # persisted


def test_failure_then_repair_then_pass_is_recorded_state_by_state(
    tmp_path, scripted_provider, scripted_sandbox
):
    svc, provider, _ = _full(
        tmp_path, scripted_provider, scripted_sandbox, [FAIL_TESTS, PASS_ALL], [_REPAIR])
    run = svc.run_pipeline("https://github.com/a/b")
    assert run.state is RunState.COMPLETED and run.verification.repair_attempts_used == 1
    states = [c.to_state for c in run.history]
    assert states[-5:] == [RunState.BUILT, RunState.VERIFYING, RunState.REPAIRING,
                           RunState.VERIFYING, RunState.COMPLETED]  # fmt: skip
    build_cart = (tmp_path / "build" / run.run_id / "cart.py").read_text()
    baseline_cart = (tmp_path / "repos" / run.run_id / "cart.py").read_text()
    assert "price * (1 - percent / 100)" in build_cart
    assert "price + price" in baseline_cart  # the untouched clone is still the baseline


def test_checks_that_keep_failing_end_the_run_failed_not_completed(
    tmp_path, scripted_provider, scripted_sandbox
):
    repair2 = json.dumps({"files": [{"path": "cart.py", "edits": [{
        "old": "    return price * (1 - percent / 100)\n",
        "new": "    return price - percent\n",
    }]}]})
    svc, _, _ = _full(tmp_path, scripted_provider, scripted_sandbox,
                      [FAIL_TESTS] * 3, [_REPAIR, repair2])  # fmt: skip
    events, cb = _collect()
    run = svc.run_pipeline("https://github.com/a/b", cb)
    assert run.state is RunState.FAILED and run.verification.outcome == "failed"
    assert run.verification.repair_attempts_used == 2 and len(run.verification.rounds) == 3
    assert run.actions[-1].name == "verify" and run.actions[-1].exit_status == "error"
    assert events[-1] == ("verify", "failed")


def test_a_sandbox_problem_completes_as_unverified_never_as_verified(
    tmp_path, scripted_provider, scripted_sandbox
):
    from ase.core.errors import SandboxError

    svc, _, _ = _full(tmp_path, scripted_provider, scripted_sandbox, [SandboxError("down")])
    run = svc.run_pipeline("https://github.com/a/b")
    assert run.state is RunState.COMPLETED and run.final_status == "completed_unverified"
    assert run.verification.outcome == "unverified"
    assert any("sandbox had a problem" in note for note in run.limitations)


def test_verification_requires_enabled_execution_and_a_built_run(
    tmp_path, scripted_provider, scripted_sandbox
):
    replies = [_GOOD_REPLY, _GOOD_PLAN, _GOOD_EDIT]
    disabled = _service(tmp_path / "off", provider=scripted_provider(replies))
    run = disabled.run_pipeline("https://github.com/a/b")
    assert disabled.environment_status().code_execution_available is False
    with pytest.raises(PolicyViolationError):
        disabled.run_verify(run.run_id)
    svc, _, _ = _full(tmp_path / "on", scripted_provider, scripted_sandbox, [PASS_ALL])
    fresh = svc.start_import("https://github.com/a/b")
    with pytest.raises(PolicyViolationError):  # imported but not built
        svc.run_verify(fresh.run_id)
