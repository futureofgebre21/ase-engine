import pytest
from pydantic import ValidationError

from ase.core.errors import InvalidTransitionError
from ase.core.models import (
    CheckResult,
    CheckStatus,
    Evidence,
    Finding,
    FindingStatus,
    RunRecord,
    Severity,
)
from ase.core.states import RunState


def _finding(**kw):
    base = dict(
        id="F-1",
        severity=Severity.MEDIUM,
        category="bug",
        description="d",
        source="static",
        confidence=0.8,
    )
    base.update(kw)
    return Finding(**base)


def test_confirmed_finding_requires_evidence():
    with pytest.raises(ValidationError):
        _finding(status=FindingStatus.CONFIRMED)
    ok = _finding(status=FindingStatus.CONFIRMED, evidence=[Evidence(file_path="a.py")])
    assert ok.status is FindingStatus.CONFIRMED


def test_unverified_finding_without_evidence_is_allowed():
    assert _finding(source="llm").status is FindingStatus.UNVERIFIED


def test_confidence_must_be_between_0_and_1():
    with pytest.raises(ValidationError):
        _finding(confidence=1.5)


def test_evidence_line_range_validation():
    with pytest.raises(ValidationError):
        Evidence(file_path="a.py", line_start=10, line_end=5)
    with pytest.raises(ValidationError):
        Evidence(file_path="a.py", line_end=5)


def test_check_cannot_claim_pass_without_exit_code_zero():
    with pytest.raises(ValidationError):
        CheckResult(name="t", command="pytest", status=CheckStatus.PASSED)
    with pytest.raises(ValidationError):
        CheckResult(name="t", command="pytest", status=CheckStatus.PASSED, exit_code=1)
    ok = CheckResult(name="t", command="pytest", status=CheckStatus.PASSED, exit_code=0)
    assert ok.status is CheckStatus.PASSED
    assert CheckResult(name="t", command="pytest", status=CheckStatus.UNVERIFIED).exit_code is None


def test_run_records_transitions_and_terminal_state():
    run = RunRecord()
    run.transition(RunState.IMPORTED)
    run.transition(RunState.FAILED, reason="clone failed")
    assert [c.to_state for c in run.history] == [RunState.IMPORTED, RunState.FAILED]
    assert run.ended_at is not None and run.final_status == "failed"
    with pytest.raises(InvalidTransitionError):
        run.transition(RunState.EXECUTING)


def test_run_cannot_execute_before_approval():
    run = RunRecord()
    for s in (RunState.IMPORTED, RunState.ANALYZED, RunState.PLAN_READY):
        run.transition(s)
    with pytest.raises(InvalidTransitionError):
        run.transition(RunState.EXECUTING)
