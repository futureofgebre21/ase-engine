"""Mock provider. Performs NO analysis. Every response is labelled ``is_mock=True``."""

from __future__ import annotations

from collections.abc import Sequence

from ase.llm.schemas import LLMRequest, LLMResponse

NO_ANALYSIS_JSON = '{"summary": "Mock provider: no analysis was performed.", "findings": []}'


class MockProvider:
    name = "mock"
    is_mock = True

    def __init__(self, responses: Sequence[str | Exception] | None = None) -> None:
        """``responses`` are scripted replies (strings) or exceptions to raise, used by tests."""
        self._queue = list(responses or [])
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        item = self._queue.pop(0) if self._queue else NO_ANALYSIS_JSON
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item, provider=self.name, model="mock-1", is_mock=True)
