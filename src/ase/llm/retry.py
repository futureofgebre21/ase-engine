"""Bounded retries with exponential backoff, plus a hard model-call budget."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from ase.core.errors import BudgetExceededError, LLMTransientError

T = TypeVar("T")


class ModelCallBudget:
    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self.used = 0

    def consume(self) -> None:
        if self.used >= self.max_calls:
            raise BudgetExceededError(f"Model call limit reached ({self.max_calls}).")
        self.used += 1


def call_with_retries(
    fn: Callable[[], T],
    *,
    attempts: int,
    base_delay: float,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Retry only ``LLMTransientError``; any other exception propagates immediately."""
    for attempt in range(attempts):
        try:
            return fn()
        except LLMTransientError:
            if attempt == attempts - 1:
                raise
            sleep(base_delay * (2**attempt))
    raise AssertionError("unreachable")  # pragma: no cover
