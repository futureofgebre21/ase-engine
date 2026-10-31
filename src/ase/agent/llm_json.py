"""Ask a model for one JSON object: bounded retries, call budget, usage accounting."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from ase.core.errors import LLMResponseError
from ase.llm.base import LLMProvider
from ase.llm.retry import ModelCallBudget, call_with_retries
from ase.llm.schemas import LLMRequest, LLMResponse

T = TypeVar("T")


@dataclass
class Usage:
    """Sums provider-reported usage. Stays None when the provider reports nothing."""

    model: str = "n/a"
    model_calls: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None

    def merge(self, other: Usage) -> None:
        self.model = other.model
        self.prompt_tokens = _add(self.prompt_tokens, other.prompt_tokens)
        self.completion_tokens = _add(self.completion_tokens, other.completion_tokens)
        if other.cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + other.cost_usd

    def add(self, resp: LLMResponse) -> None:
        self.model = resp.model
        if resp.prompt_tokens is not None:
            self.prompt_tokens = (self.prompt_tokens or 0) + resp.prompt_tokens
        if resp.completion_tokens is not None:
            self.completion_tokens = (self.completion_tokens or 0) + resp.completion_tokens
        if resp.cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + resp.cost_usd


def _add(a: int | None, b: int | None) -> int | None:
    return a if b is None else (a or 0) + b


def ask_for_json(
    provider: LLMProvider,
    request: LLMRequest,
    parse: Callable[[str], T],
    *,
    budget: ModelCallBudget,
    parse_attempts: int,
    transient_attempts: int,
    base_delay: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[T, Usage]:
    """Return ``(parsed, usage)`` or raise. Every attempt counts against ``budget``."""
    usage = Usage()

    def call() -> LLMResponse:
        budget.consume()
        return provider.complete(request)

    last_error: LLMResponseError | None = None
    for _ in range(parse_attempts):
        resp = call_with_retries(
            call, attempts=transient_attempts, base_delay=base_delay, sleep=sleep
        )
        usage.add(resp)
        try:
            result = parse(resp.text)
        except LLMResponseError as exc:
            last_error = exc
            continue
        usage.model_calls = budget.used
        return result, usage
    raise last_error or LLMResponseError("The model returned no usable response.")
