"""Adapter tests against a throwaway local HTTP server (test code only)."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ase.core.errors import (
    AseError,
    ConfigurationError,
    LLMRequestError,
    LLMResponseError,
    LLMTransientError,
)
from ase.llm.openai_compatible import OpenAICompatibleProvider
from ase.llm.schemas import ContextFile, LLMRequest

KEY = "test-key-abc123"


class _Server:
    """Serves one scripted behaviour and records what it received."""

    def __init__(self, status=200, body=None, delay=0.0, headers=None):
        outer = self
        self.seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.seen.append(
                    {"path": self.path, "auth": self.headers.get("Authorization"),
                     "body": json.loads(self.rfile.read(length) or b"{}")}
                )  # fmt: skip
                if delay:
                    time.sleep(delay)
                payload = json.dumps(body if body is not None else {}).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):  # keep test output quiet
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _ok(content="hello", **extra):
    return {"model": "m-1", "choices": [{"message": {"content": content}}], **extra}


def _provider(url, timeout=5.0, key=KEY):
    return OpenAICompatibleProvider(
        base_url=url, model="meta/test-model", api_key=key, timeout_seconds=timeout,
        allow_insecure_localhost=True,
    )  # fmt: skip


def _request():
    return LLMRequest(
        system="sys", task="task", prompt_version="v", max_output_tokens=123,
        context_files=[ContextFile(path="a.py", content="x = 1")],
    )  # fmt: skip


def test_success_sends_the_right_request_and_parses_usage():
    server = _Server(body=_ok("answer", usage={"prompt_tokens": 11, "completion_tokens": 7}))
    try:
        resp = _provider(server.url).complete(_request())
    finally:
        server.close()
    sent = server.seen[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["auth"] == f"Bearer {KEY}"
    assert sent["body"]["model"] == "meta/test-model" and sent["body"]["max_tokens"] == 123
    assert [m["role"] for m in sent["body"]["messages"]] == ["system", "user"]
    assert "x = 1" in sent["body"]["messages"][1]["content"]  # file content reaches the model
    assert resp.text == "answer" and resp.model == "m-1" and resp.provider == "openai_compatible"
    assert (resp.prompt_tokens, resp.completion_tokens) == (11, 7)
    assert resp.cost_usd is None  # never estimated


def test_missing_usage_stays_unknown():
    server = _Server(body=_ok())
    try:
        resp = _provider(server.url).complete(_request())
    finally:
        server.close()
    assert resp.prompt_tokens is None and resp.completion_tokens is None


def test_status_codes_map_to_the_right_error_types():
    cases = [
        (401, ConfigurationError), (403, ConfigurationError),
        (429, LLMTransientError), (503, LLMTransientError), (500, LLMTransientError),
        (400, LLMRequestError), (404, LLMRequestError),
    ]  # fmt: skip
    for status, expected in cases:
        server = _Server(status=status, body={"error": "nope"})
        try:
            with pytest.raises(expected):
                _provider(server.url).complete(_request())
        finally:
            server.close()


def test_api_key_is_never_leaked_in_errors():
    server = _Server(status=400, body={"error": f"bad key {KEY} and Bearer {KEY}"})
    try:
        with pytest.raises(AseError) as info:
            _provider(server.url).complete(_request())
    finally:
        server.close()
    assert KEY not in str(info.value) and KEY not in repr(_provider(server.url))


def test_timeout_is_a_transient_error():
    server = _Server(body=_ok(), delay=1.0)
    try:
        with pytest.raises(LLMTransientError):
            _provider(server.url, timeout=0.2).complete(_request())
    finally:
        server.close()


def test_connection_refused_is_transient():
    server = _Server()
    url = server.url
    server.close()
    with pytest.raises(LLMTransientError):
        _provider(url).complete(_request())


def test_empty_or_malformed_replies_are_response_errors():
    for body in (_ok(""), _ok("   "), {"choices": []}, {"nope": 1}, {"choices": [{"message": {}}]}):
        server = _Server(body=body)
        try:
            with pytest.raises(LLMResponseError):
                _provider(server.url).complete(_request())
        finally:
            server.close()


def test_list_of_text_parts_is_joined():
    server = _Server(body=_ok([{"type": "text", "text": "ab"}, {"type": "text", "text": "cd"}]))
    try:
        assert _provider(server.url).complete(_request()).text == "abcd"
    finally:
        server.close()


def test_redirects_are_not_followed_so_the_key_cannot_be_forwarded():
    target = _Server(body=_ok("stolen"))
    origin = _Server(status=302, headers={"Location": f"{target.url}/chat/completions"})
    try:
        with pytest.raises(AseError):
            _provider(origin.url).complete(_request())
    finally:
        origin.close()
        target.close()
    assert target.seen == []  # the redirect target was never contacted


def test_constructor_rejects_unsafe_configuration():
    bad_urls = [
        "http://example.com/v1",
        "ftp://x/v1",
        "https://user:pw@host/v1",
        "https://host/v1?x=1",
    ]
    for url in bad_urls:
        with pytest.raises(ConfigurationError):
            OpenAICompatibleProvider(base_url=url, model="m", api_key=KEY)
    with pytest.raises(ConfigurationError):  # plain http to localhost needs explicit opt-in
        OpenAICompatibleProvider(base_url="http://127.0.0.1:9/v1", model="m", api_key=KEY)
    for key in ("", "   ", "has space", "bad\nkey", "k\x00ey", "kéy"):
        with pytest.raises(ConfigurationError):
            OpenAICompatibleProvider(base_url="https://host/v1", model="m", api_key=key)
    with pytest.raises(ConfigurationError):
        OpenAICompatibleProvider(base_url="https://host/v1", model=" ", api_key=KEY)


def test_key_with_surrounding_whitespace_is_trimmed():
    server = _Server(body=_ok())
    try:
        _provider(server.url, key=f"  {KEY}\r\n").complete(_request())
    finally:
        server.close()
    assert server.seen[0]["auth"] == f"Bearer {KEY}"
