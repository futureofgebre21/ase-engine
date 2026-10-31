"""Provider-neutral interface. Provider SDK/HTTP code lives only inside adapters."""

from __future__ import annotations

from typing import Protocol

from ase.llm.schemas import LLMRequest, LLMResponse


class LLMProvider(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return the model's reply.

        Must raise ``LLMTransientError`` for retryable failures (timeouts, rate limits, 5xx)
        and ``AseError`` subclasses for everything else. Must never log API keys.
        """
        ...
