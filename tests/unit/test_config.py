import pytest
from pydantic import ValidationError

from ase.core.config import Settings


def _settings(**kw):
    return Settings(_env_file=None, **kw)


def test_defaults_are_safe():
    s = _settings()
    assert s.llm_provider == "none" and s.llm_configured is False
    assert s.allow_code_execution is False
    assert s.max_repair_attempts == 2
    assert s.git_check_cert_revocation is True  # secure unless the user opts out


def test_real_provider_requires_credentials():
    with pytest.raises(ValidationError):
        _settings(llm_provider="openai_compatible")


def test_real_provider_with_all_fields_is_valid():
    s = _settings(
        llm_provider="openai_compatible",
        llm_base_url="https://example.invalid/v1",
        llm_model="m",
        llm_api_key="not-a-real-key",
    )
    assert s.llm_configured is True


def test_api_key_is_not_leaked_in_repr():
    s = _settings(llm_api_key="super-secret-value")
    assert "super-secret-value" not in repr(s)


def test_limits_must_be_positive():
    with pytest.raises(ValidationError):
        _settings(max_model_calls=0)
    with pytest.raises(ValidationError):
        _settings(max_repair_attempts=99)


def test_env_variables_are_read(monkeypatch):
    monkeypatch.setenv("ASE_MAX_FILES", "123")
    assert Settings(_env_file=None).max_files == 123
