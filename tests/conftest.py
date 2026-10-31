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
