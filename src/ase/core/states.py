"""Run state machine. Pure logic, no I/O, easy to test."""

from __future__ import annotations

from enum import StrEnum

from ase.core.errors import InvalidTransitionError


class RunState(StrEnum):
    CREATED = "created"
    IMPORTED = "imported"
    ANALYZED = "analyzed"
    PLAN_READY = "plan_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    BUILT = "built"  # code written in the isolated copy, NOT yet verified
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"  # waiting on something the agent cannot do alone


TERMINAL_STATES: frozenset[RunState] = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
)

_S = RunState
ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    _S.CREATED: frozenset({_S.IMPORTED, _S.FAILED, _S.CANCELLED, _S.BLOCKED}),
    _S.IMPORTED: frozenset({_S.ANALYZED, _S.FAILED, _S.CANCELLED}),
    # ANALYZED -> COMPLETED allows an audit-only run (no changes requested).
    _S.ANALYZED: frozenset({_S.PLAN_READY, _S.COMPLETED, _S.FAILED, _S.CANCELLED}),
    _S.PLAN_READY: frozenset({_S.AWAITING_APPROVAL, _S.FAILED, _S.CANCELLED, _S.BLOCKED}),
    # Rejection sends the run back to PLAN_READY so a new plan can be made.
    _S.AWAITING_APPROVAL: frozenset({_S.EXECUTING, _S.PLAN_READY, _S.CANCELLED, _S.BLOCKED}),
    _S.EXECUTING: frozenset({_S.BUILT, _S.VERIFYING, _S.FAILED, _S.CANCELLED, _S.BLOCKED}),
    _S.BUILT: frozenset({_S.VERIFYING, _S.FAILED, _S.CANCELLED}),
    _S.VERIFYING: frozenset({_S.COMPLETED, _S.REPAIRING, _S.FAILED, _S.CANCELLED}),
    _S.REPAIRING: frozenset({_S.VERIFYING, _S.FAILED, _S.CANCELLED}),
    _S.BLOCKED: frozenset({_S.AWAITING_APPROVAL, _S.FAILED, _S.CANCELLED}),
    _S.COMPLETED: frozenset(),
    _S.FAILED: frozenset(),
    _S.CANCELLED: frozenset(),
}


def is_terminal(state: RunState) -> bool:
    return state in TERMINAL_STATES


def can_transition(current: RunState, new: RunState) -> bool:
    return new in ALLOWED_TRANSITIONS[current]


def assert_transition(current: RunState, new: RunState) -> None:
    if not can_transition(current, new):
        raise InvalidTransitionError(f"Cannot move run from '{current.value}' to '{new.value}'.")
