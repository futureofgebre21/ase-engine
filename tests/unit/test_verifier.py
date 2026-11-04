import json
import shutil
from pathlib import Path

from ase.agent.repair import Repairer, RepairLimits
from ase.agent.verifier import Verifier, VerifyLimits, compute_outcome
from ase.core.errors import SandboxError
from ase.core.models import CheckResult, CheckStatus, Plan, PlannedChange
from ase.llm.retry import ModelCallBudget

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"
BUG = "    return price + price * percent / 100\n"
FIX_1 = "    return price - price * percent / 100\n"
FIX_2 = "    return price * (1 - percent / 100)\n"
PASS_ALL = {"compile": (0, ""), "install": (0, ""), "install-test-runner": (0, ""),
            "tests": (0, "2 passed in 0.1s")}  # fmt: skip
FAIL_TESTS = {
    "compile": (0, ""), "install": (0, ""), "install-test-runner": (0, ""),
    "tests": (1, "FAILED test_discount_reduces_price - assert 110.0 == 90.0"),
}  # fmt: skip


def _repair_reply(old, new, path="cart.py"):
    return json.dumps({"files": [{"path": path, "edits": [{"old": old, "new": new}]}]})


def _plan():
    change = PlannedChange(id="C-001", action="modify", path="cart.py", summary="Subtract",
                           finding_ids=["F-001"], verification="tests", risk="low")  # fmt: skip
    return Plan(changes=[change], architecture_summary="tiny", provider="p", model="m",
                prompt_version="plan-v1")  # fmt: skip


def _setup(tmp_path, scripted_sandbox, scripted_provider, script, replies=(), repairs=2,
           with_repairer=True):  # fmt: skip
    build_dir = tmp_path / "build"
    shutil.copytree(FIXTURE, build_dir)
    sandbox = scripted_sandbox(script)
    provider = scripted_provider(list(replies))
    repairer = None
    if with_repairer:
        limits = RepairLimits(60_000, 100_000, base_delay_seconds=0.0)
        repairer = Repairer(provider, limits, sleep=lambda s: None)
    verifier = Verifier(sandbox, repairer, VerifyLimits(repairs, 120, 1000, 10**7, 10**6))
    states = []

    def verify():
        return verifier.verify(build_dir=build_dir, plan=_plan(), budget=ModelCallBudget(20),
                               on_state=states.append)  # fmt: skip

    return build_dir, sandbox, provider, states, verify


def test_passing_tests_in_the_sandbox_are_a_pass(tmp_path, scripted_sandbox, scripted_provider):
    _, sandbox, provider, states, verify = _setup(
        tmp_path, scripted_sandbox, scripted_provider, [PASS_ALL])
    report = verify()
    assert report.outcome == "passed" and len(report.rounds) == 1
    names = [c.name for c in report.rounds[0].checks]
    assert names == ["python:compile", "python:install", "python:install-test-runner",
                     "python:tests"]  # fmt: skip
    assert all(c.status is CheckStatus.PASSED and c.exit_code == 0 for c in report.rounds[0].checks)
    assert report.sandbox == "scripted-sandbox" and "pypi.org" in report.network_policy
    assert provider.requests == [] and states == ["verifying"]  # no repair was needed
    assert report.repair_attempts_used == 0 and report.model_calls == 0


def test_failing_tests_are_repaired_and_reverified(tmp_path, scripted_sandbox, scripted_provider):
    build_dir, _, provider, states, verify = _setup(
        tmp_path, scripted_sandbox, scripted_provider, [FAIL_TESTS, PASS_ALL],
        [_repair_reply(BUG, FIX_1)])  # fmt: skip
    report = verify()
    assert report.outcome == "passed" and len(report.rounds) == 2
    assert report.rounds[0].outcome == "failed" and report.rounds[1].outcome == "passed"
    fix = report.rounds[0].repaired[0]
    assert fix.status == "applied" and fix.change_id == "R1-1"
    assert "+    return price - price" in fix.diff
    assert FIX_1 in (build_dir / "cart.py").read_text()
    assert states == ["verifying", "repairing", "verifying"]
    assert report.repair_attempts_used == 1 and report.model_calls == 1
    # the model saw the real failure, as untrusted data
    req = provider.requests[0]
    output = next(f for f in req.context_files if f.path == "TEST_OUTPUT.txt")
    assert "assert 110.0 == 90.0" in output.content and "assert 110.0" not in req.system


def test_the_repair_loop_is_bounded_and_ends_failed(tmp_path, scripted_sandbox, scripted_provider):
    replies = [_repair_reply(BUG, FIX_1), _repair_reply(FIX_1, FIX_2), _repair_reply(FIX_2, BUG)]
    _, sandbox, provider, states, verify = _setup(
        tmp_path, scripted_sandbox, scripted_provider, [FAIL_TESTS] * 3, replies, repairs=2)
    report = verify()
    assert report.outcome == "failed" and len(report.rounds) == 3
    assert report.repair_attempts_used == 2 and len(provider.requests) == 2  # never a 3rd repair
    assert len(sandbox.opened) == 3


def test_a_repair_that_cannot_be_applied_stops_the_loop(
    tmp_path, scripted_sandbox, scripted_provider
):
    bad = _repair_reply("    return nothing like this\n", "x")
    _, _, provider, _, verify = _setup(
        tmp_path, scripted_sandbox, scripted_provider, [FAIL_TESTS, FAIL_TESTS], [bad])
    report = verify()
    assert report.outcome == "failed" and len(report.rounds) == 1
    assert report.rounds[0].repaired[0].status == "failed"
    assert report.repair_attempts_used == 0


def test_repair_cannot_touch_files_outside_the_approved_scope(
    tmp_path, scripted_sandbox, scripted_provider
):
    reply = _repair_reply("from cart import cart_total", "import os", path="main.py")
    build_dir, _, _, _, verify = _setup(
        tmp_path, scripted_sandbox, scripted_provider, [FAIL_TESTS], [reply])
    report = verify()
    change = report.rounds[0].repaired[0]
    assert change.status == "failed" and "outside the approved scope" in change.reason
    assert "from cart import cart_total" in (build_dir / "main.py").read_text()


def test_without_a_repairer_a_failure_is_simply_reported(
    tmp_path, scripted_sandbox, scripted_provider
):
    _, _, _, _, verify = _setup(
        tmp_path, scripted_sandbox, scripted_provider, [FAIL_TESTS], with_repairer=False)
    report = verify()
    assert report.outcome == "failed" and report.rounds[0].repaired == []


def test_a_failed_install_skips_the_rest_and_is_a_failure(
    tmp_path, scripted_sandbox, scripted_provider
):
    script = [{"compile": (0, ""), "install": (1, "No matching distribution found")}]
    _, _, _, _, verify = _setup(tmp_path, scripted_sandbox, scripted_provider, script,
                                with_repairer=False)  # fmt: skip
    checks = {c.name: c for c in verify().rounds[0].checks}
    assert checks["python:install"].status is CheckStatus.FAILED
    assert checks["python:install-test-runner"].status is CheckStatus.SKIPPED
    assert checks["python:tests"].status is CheckStatus.SKIPPED
    assert "earlier check failed" in checks["python:tests"].output


def test_a_sandbox_problem_is_unverified_not_failed(tmp_path, scripted_sandbox, scripted_provider):
    _, _, provider, _, verify = _setup(
        tmp_path, scripted_sandbox, scripted_provider, [SandboxError("could not start")])
    report = verify()
    assert report.outcome == "unverified" and provider.requests == []  # no repair for infra errors
    assert "sandbox had a problem" in report.rounds[0].unverified_reasons[0]


def test_no_tests_means_unverified_never_passed(tmp_path, scripted_sandbox, scripted_provider):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "app.py").write_text("print(1)\n")
    verifier = Verifier(scripted_sandbox([{"compile": (0, "")}]), None,
                        VerifyLimits(2, 120, 1000, 10**7, 10**6))  # fmt: skip
    report = verifier.verify(build_dir=build_dir, plan=_plan(), budget=ModelCallBudget(5))
    assert report.outcome == "unverified"
    checks = {c.name: c.status for c in report.rounds[0].checks}
    assert checks == {"python:compile": CheckStatus.PASSED, "python:tests": CheckStatus.SKIPPED}
    assert any("No tests ran" in r for r in report.rounds[0].unverified_reasons)


def test_unsupported_ecosystems_never_open_a_sandbox(tmp_path, scripted_sandbox, scripted_provider):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "go.mod").write_text("module x\n")
    (build_dir / "main.go").write_text("package main\n")
    sandbox = scripted_sandbox([])
    verifier = Verifier(sandbox, None, VerifyLimits(2, 120, 1000, 10**7, 10**6))
    report = verifier.verify(build_dir=build_dir, plan=_plan(), budget=ModelCallBudget(5))
    assert report.outcome == "unverified" and sandbox.opened == []
    assert "'go' ecosystem" in report.rounds[0].unverified_reasons[0]


def test_passing_python_does_not_hide_an_unverified_part(
    tmp_path, scripted_sandbox, scripted_provider
):
    build_dir = tmp_path / "build"
    shutil.copytree(FIXTURE, build_dir)
    (build_dir / "svc").mkdir()
    (build_dir / "svc" / "go.mod").write_text("module x\n")
    limits = VerifyLimits(2, 120, 1000, 10**7, 10**6)
    verifier = Verifier(scripted_sandbox([PASS_ALL]), None, limits)
    report = verifier.verify(build_dir=build_dir, plan=_plan(), budget=ModelCallBudget(5))
    assert report.outcome == "unverified"  # honest: the go part is unproven
    assert any(c.status is CheckStatus.PASSED for c in report.rounds[0].checks)


def _check(name, status, code=None):
    return CheckResult(name=name, command="x", status=status, exit_code=code)


def test_outcome_rules():
    ok = _check("python:tests", CheckStatus.PASSED, 0)
    bad = _check("python:tests", CheckStatus.FAILED, 1)
    skipped = _check("python:tests", CheckStatus.SKIPPED)
    assert compute_outcome([ok], []) == "passed"
    assert compute_outcome([ok], ["something unproven"]) == "unverified"
    assert compute_outcome([bad], []) == "failed" and compute_outcome([ok, bad], []) == "failed"
    assert compute_outcome([skipped], []) == "unverified"
    assert compute_outcome([], []) == "unverified"
    compile_only = _check("python:compile", CheckStatus.PASSED, 0)
    assert compute_outcome([compile_only], []) == "unverified"  # compiling is not testing
