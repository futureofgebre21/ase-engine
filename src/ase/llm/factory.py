from __future__ import annotations

from ase.core.config import Settings
from ase.llm.base import LLMProvider
from ase.llm.openai_compatible import OpenAICompatibleProvider


def build_provider(settings: Settings) -> LLMProvider | None:
    """Return the configured provider, or ``None`` when no provider is configured."""
    if settings.llm_provider == "none":
        return None
    # Settings validation guarantees these are present for a real provider.
    if not (settings.llm_api_key and settings.llm_base_url and settings.llm_model):
        return None
    return OpenAICompatibleProvider(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key.get_secret_value(),
        timeout_seconds=float(settings.llm_timeout_seconds),
    )
