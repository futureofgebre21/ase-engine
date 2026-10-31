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
    NEXT_STEPS_NOTE,
    NO_PROVIDER_NOTE,
    NOTHING_TO_PLAN_NOTE,
    UNTRUSTED_NOTE,
    ProjectService,
)
from ase.storage.run_store import JsonRunStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"


def _copy_fixture(url, dest, timeout):
    shutil.copytree(FIXTURE, dest)


def _service(tmp_path, clone_fn=_copy_fixture, provider=None):
    settings = Settings(_env_file=None, workspace_root=tmp_path)
    importer = RepositoryImporter(tmp_path, ImportLimits.from_settings(settings), clone_fn)
    return ProjectService(settings, JsonRunStore(tmp_path), importer, provider)


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


def test_pipeline_runs_steps_1_to_4_and_reports_real_progress(tmp_path, scripted_provider):
    svc = _service(tmp_path, provider=scripted_provider([_GOOD_REPLY, _GOOD_PLAN, _GOOD_EDIT]))
    events, cb = _collect()
    run = svc.run_pipeline("https://github.com/a/b", cb)
    assert run.state is RunState.BUILT and run.plan is not None and run.build is not None
    assert (run.build.applied, run.build.failed) == (1, 0)
    assert events == [
        ("import", "running"), ("import", "done"), ("audit", "running"), ("audit", "done"),
        ("plan", "running"), ("plan", "done"), ("build", "running"),
        ("build", "C-001: modify cart.py"), ("build", "done"),
    ]  # fmt: skip
    # the approval and execution are on the record, in order
    assert [c.to_state for c in run.history][-3:] == [
        RunState.AWAITING_APPROVAL, RunState.EXECUTING, RunState.BUILT]  # fmt: skip
    assert run.approved_scope == ["cart.py"] and NEXT_STEPS_NOTE in run.limitations
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
