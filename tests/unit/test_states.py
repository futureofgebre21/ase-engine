import pytest

from ase.core.errors import InvalidTransitionError
from ase.core.states import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    RunState,
    assert_transition,
    can_transition,
)


def test_every_state_has_a_transition_entry():
    assert set(ALLOWED_TRANSITIONS) == set(RunState)


def test_terminal_states_have_no_exits():
    for state in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[state] == frozenset()


def test_happy_path_is_allowed():
    path = [
        RunState.CREATED,
        RunState.IMPORTED,
        RunState.ANALYZED,
        RunState.PLAN_READY,
        RunState.AWAITING_APPROVAL,
        RunState.EXECUTING,
        RunState.VERIFYING,
        RunState.COMPLETED,
    ]
    for current, new in zip(path, path[1:], strict=False):
        assert can_transition(current, new)


def test_built_code_must_still_be_verified_before_completion():
    assert can_transition(RunState.EXECUTING, RunState.BUILT)
    assert can_transition(RunState.BUILT, RunState.VERIFYING)
    assert not can_transition(RunState.BUILT, RunState.COMPLETED)
    assert not can_transition(RunState.PLAN_READY, RunState.BUILT)


def test_cannot_skip_approval():
    assert not can_transition(RunState.PLAN_READY, RunState.EXECUTING)
    with pytest.raises(InvalidTransitionError):
        assert_transition(RunState.PLAN_READY, RunState.EXECUTING)


def test_cannot_leave_terminal_state():
    with pytest.raises(InvalidTransitionError):
        assert_transition(RunState.COMPLETED, RunState.EXECUTING)


def test_repair_must_return_to_verifying():
    assert can_transition(RunState.REPAIRING, RunState.VERIFYING)
    assert not can_transition(RunState.REPAIRING, RunState.COMPLETED)
