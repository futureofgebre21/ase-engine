"""Adapter for any OpenAI-compatible ``/chat/completions`` server (for example Baseten).

Standard library only. Safety properties:
- https only (plain http is allowed solely for localhost, and only when explicitly enabled)
- the API key is validated, never logged, and scrubbed from any error text
- redirects are NOT followed, so the Authorization header can never be forwarded elsewhere
- bounded response size and a hard timeout
- retryable failures raise ``LLMTransientError``; everything else is a clear, non-retryable error
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from ase.core.errors import (
    AseError,
    ConfigurationError,
    LLMRequestError,
    LLMResponseError,
    LLMTransientError,
)
from ase.llm.prompts.audit import render_user_message
from ase.llm.schemas import LLMRequest, LLMResponse
from ase.security.redaction import redact

_MAX_RESPONSE_BYTES = 4_000_000
_TRANSIENT_STATUS = frozenset({408, 409, 425, 429})
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # turns any 3xx into an HTTPError instead of following it


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 300.0,
        allow_insecure_localhost: bool = False,
    ) -> None:
        parts = urlsplit(base_url)
        host = (parts.hostname or "").lower()
        local_http = allow_insecure_localhost and parts.scheme == "http" and host in _LOCAL_HOSTS
        if parts.scheme != "https" and not local_http:
            raise ConfigurationError("ASE_LLM_BASE_URL must start with https://")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ConfigurationError("The AI server URL must not contain credentials or a query.")

        key = api_key.strip()
        if not key or not key.isascii() or not key.isprintable() or " " in key:
            raise ConfigurationError(
                "The API key (ASE_LLM_API_KEY) is empty or contains invalid characters "
                "(for example a hidden line break). Copy it again without extra characters."
            )
        if not model.strip():
            raise ConfigurationError("The model name (ASE_LLM_MODEL) is empty.")

        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model.strip()
        self._api_key = key
        self._timeout = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirect)

    def __repr__(self) -> str:  # never include the key
        return f"OpenAICompatibleProvider(model={self._model!r})"

    # ------------------------------------------------------------------ public

    def complete(self, request: LLMRequest) -> LLMResponse:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": render_user_message(request)},
            ],
            "max_tokens": request.max_output_tokens,
        }
        http_request = urllib.request.Request(  # noqa: S310 - scheme validated in __init__
            self._url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        try:
            with self._opener.open(http_request, timeout=self._timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise self._error_for_status(exc) from None
        except OSError as exc:  # URLError, TimeoutError and ConnectionError are OSErrors
            raise LLMTransientError(
                "Could not reach the AI server (network problem or timeout)."
            ) from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise LLMResponseError("The AI server's response was too large.")
        return self._parse(raw)

    # ----------------------------------------------------------------- helpers

    def _scrub(self, text: str) -> str:
        return redact(text.replace(self._api_key, "[REDACTED]"))

    def _error_for_status(self, exc: urllib.error.HTTPError) -> AseError:
        try:
            snippet = self._scrub(exc.read(400).decode("utf-8", errors="replace")).strip()
        except OSError:
            snippet = ""
        code = exc.code
        if code in (401, 403):
            return ConfigurationError(
                f"The AI server rejected the API key (HTTP {code}). Check ASE_LLM_API_KEY "
                "and that the key is allowed to use this model."
            )
        if code in _TRANSIENT_STATUS or 500 <= code < 600:
            return LLMTransientError(f"The AI server is busy or failing (HTTP {code}). {snippet}")
        return LLMRequestError(f"The AI server refused the request (HTTP {code}). {snippet}")

    def _parse(self, raw: bytes) -> LLMResponse:
        try:
            payload = json.loads(raw)
            choice = payload["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("The AI server's reply was not in the expected format.") from exc

        if isinstance(content, list):  # some servers return a list of text parts
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError(
                "The model returned no final answer. Reasoning models can spend all output "
                "tokens thinking; try a larger ASE_LLM_MAX_OUTPUT_TOKENS."
            )

        usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        return LLMResponse(
            text=content,
            provider=self.name,
            model=str(payload.get("model") or self._model),
            prompt_tokens=_as_int(usage.get("prompt_tokens")),
            completion_tokens=_as_int(usage.get("completion_tokens")),
            cost_usd=None,  # the server does not report cost; we never estimate it here
        )


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
