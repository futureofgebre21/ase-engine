import pytest

from ase.core.errors import AseError, BudgetExceededError, LLMTransientError
from ase.llm.retry import ModelCallBudget, call_with_retries


def test_transient_errors_are_retried_with_exponential_backoff():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise LLMTransientError("rate limited")
        return "ok"

    assert call_with_retries(flaky, attempts=3, base_delay=1.0, sleep=sleeps.append) == "ok"
    assert len(calls) == 3 and sleeps == [1.0, 2.0]


def test_gives_up_after_max_attempts():
    calls = []

    def always():
        calls.append(1)
        raise LLMTransientError("down")

    with pytest.raises(LLMTransientError):
        call_with_retries(always, attempts=3, base_delay=0, sleep=lambda s: None)
    assert len(calls) == 3


def test_non_transient_errors_are_not_retried():
    calls = []

    def bad():
        calls.append(1)
        raise AseError("nope")

    with pytest.raises(AseError):
        call_with_retries(bad, attempts=5, base_delay=0, sleep=lambda s: None)
    assert len(calls) == 1


def test_budget_blocks_extra_calls():
    b = ModelCallBudget(2)
    b.consume()
    b.consume()
    with pytest.raises(BudgetExceededError):
        b.consume()
    assert b.used == 2
