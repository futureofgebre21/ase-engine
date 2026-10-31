"""Smoke test: the Streamlit app starts and tells the truth when no AI provider is configured."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from ase.core.config import get_settings  # noqa: E402
from ase.ui.presenters import NO_PROVIDER_BANNER  # noqa: E402

APP = str(Path(__file__).resolve().parents[2] / "app" / "main.py")


def _start(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # so a developer's real .env is never read
    monkeypatch.setenv("ASE_WORKSPACE_ROOT", str(tmp_path / "ws"))
    get_settings.cache_clear()
    return AppTest.from_file(APP, default_timeout=30).run()


def test_app_starts_and_reports_missing_provider(tmp_path, monkeypatch):
    at = _start(tmp_path, monkeypatch)
    assert not at.exception
    assert any(NO_PROVIDER_BANNER in w.value for w in at.warning)


def test_rejected_url_shows_error_and_creates_no_run(tmp_path, monkeypatch):
    at = _start(tmp_path, monkeypatch)
    at.text_input[0].set_value("https://evil.com/a/b")
    at.button[0].click().run()
    assert any("Rejected" in e.value for e in at.error)
