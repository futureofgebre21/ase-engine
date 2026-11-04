import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_ase_environment(monkeypatch):
    """Tests must not depend on ASE_* variables set in the developer's shell."""
    for key in list(os.environ):
        if key.startswith("ASE_"):
            monkeypatch.delenv(key)


@pytest.fixture
def make_symlink():
    """Create a symlink, or SKIP the test if this system does not allow it.

    On Windows, creating symlinks needs Developer Mode (or an administrator shell).
    """

    def _make(target, link):
        try:
            os.symlink(target, link, target_is_directory=Path(target).is_dir())
        except OSError:
            pytest.skip("Symlinks not permitted here (Windows: enable Developer Mode).")

    return _make


class ScriptedProvider:
    """TEST-ONLY stand-in for a model server. Lives in tests/, never imported by the app.

    Replays replies (or raises exceptions) written by the test, in order. When the script is
    exhausted it fails loudly instead of inventing output.
    """

    name = "scripted-test-double"

    def __init__(self, replies=None):
        self._queue = list(replies or [])
        self.requests = []

    def complete(self, request):
        from ase.llm.schemas import LLMResponse

        self.requests.append(request)
        if not self._queue:
            raise AssertionError("ScriptedProvider script exhausted: test asked for more replies")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item, provider=self.name, model="scripted-1")


@pytest.fixture
def scripted_provider():
    return ScriptedProvider


class _ScriptedSession:
    def __init__(self, outputs):
        self._outputs = outputs

    def run(self, spec):
        from ase.core.models import CheckResult, CheckStatus

        if spec.name not in self._outputs:
            raise AssertionError(f"ScriptedSandbox has no scripted result for '{spec.name}'")
        code, text = self._outputs[spec.name]
        return CheckResult(
            name=spec.name, command=spec.command, exit_code=code, output=text,
            status=CheckStatus.PASSED if code == 0 else CheckStatus.FAILED,
            duration_seconds=0.1,
        )  # fmt: skip


class _ScriptedSessionContext:
    def __init__(self, outputs):
        self._outputs = outputs

    def __enter__(self):
        return _ScriptedSession(self._outputs)

    def __exit__(self, *exc_info):
        return False


class ScriptedSandbox:
    """TEST-ONLY stand-in for the Modal sandbox. Lives in tests/, never imported by the app.

    Each ``open()`` consumes one scripted item: a dict ``{check name: (exit code, output)}`` or
    an exception to raise. It never executes anything and fails loudly if the script runs out.
    """

    name = "scripted-sandbox"

    def __init__(self, script=None):
        self._script = list(script or [])
        self.opened = []

    def open(self, build_dir, profile):
        self.opened.append(profile.name)
        if not self._script:
            raise AssertionError("ScriptedSandbox script exhausted: more rounds than scripted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _ScriptedSessionContext(item)


@pytest.fixture
def scripted_sandbox():
    return ScriptedSandbox
