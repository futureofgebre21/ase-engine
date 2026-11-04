"""Typed configuration loaded from environment variables (prefix ``ASE_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


STAGE_ORDER = ("audit", "plan", "build")  # steps that call an AI model, in pipeline order
STAGES = frozenset(STAGE_ORDER)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASE_", env_file=".env", extra="ignore", frozen=True
    )

    # --- LLM: "none" means no AI provider is configured (the app says so; it never fakes) ---
    llm_provider: Literal["none", "openai_compatible"] = "none"
    llm_label: str = Field(default="llm", pattern=r"^[A-Za-z0-9_.-]{1,30}$")  # shown in the UI
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    # Optional second OpenAI-compatible provider (for example a Modal Endpoint). The stages named
    # in llm2_stages use it; every other stage uses the first provider.
    llm2_label: str = Field(default="llm2", pattern=r"^[A-Za-z0-9_.-]{1,30}$")
    llm2_base_url: str | None = None
    llm2_model: str | None = None
    llm2_api_key: SecretStr | None = None
    llm2_stages: str = "build"  # comma-separated subset of: audit, plan, build
    # Reasoning models spend output tokens thinking, so the limit must be generous.
    llm_max_output_tokens: int = Field(default=8_000, gt=0, le=64_000)
    llm_timeout_seconds: int = Field(default=300, gt=0, le=1_800)

    # --- GitHub (least privilege; optional) ---
    github_token: SecretStr | None = None

    # --- Workspace ---
    workspace_root: Path = Path("workspaces")

    # --- Safety limits ---
    max_repo_bytes: int = Field(default=50_000_000, gt=0)
    max_file_bytes: int = Field(default=1_000_000, gt=0)
    max_files: int = Field(default=5_000, gt=0)
    max_model_calls: int = Field(default=20, gt=0)
    max_repair_attempts: int = Field(default=2, ge=0, le=5)
    max_exec_seconds: int = Field(default=120, gt=0, le=3_600)
    max_output_bytes: int = Field(default=65_536, gt=0)
    max_context_files: int = Field(default=12, gt=0, le=100)
    max_context_bytes: int = Field(default=60_000, gt=0)
    # Windows (schannel) sometimes cannot reach certificate-revocation servers. Turning this off
    # keeps TLS encryption and hostname checks but stops checking for revoked certificates.
    git_check_cert_revocation: bool = True
    max_build_model_calls: int = Field(default=40, gt=0, le=200)
    max_generated_file_bytes: int = Field(default=200_000, gt=0)
    max_plan_changes: int = Field(default=15, gt=0, le=50)
    import_timeout_seconds: int = Field(default=120, gt=0, le=1_800)

    # Off by default: running repository code requires a real sandbox.
    allow_code_execution: bool = False

    @model_validator(mode="after")
    def _real_provider_needs_credentials(self) -> Settings:
        if self.llm_provider != "none":
            missing = [
                name
                for name, value in (
                    ("ASE_LLM_API_KEY", self.llm_api_key),
                    ("ASE_LLM_BASE_URL", self.llm_base_url),
                    ("ASE_LLM_MODEL", self.llm_model),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"llm_provider='{self.llm_provider}' requires: {', '.join(missing)}"
                )
        second = (self.llm2_base_url, self.llm2_model, self.llm2_api_key)
        if any(second) and not all(second):
            raise ValueError(
                "A second AI provider needs ASE_LLM2_BASE_URL, ASE_LLM2_MODEL and "
                "ASE_LLM2_API_KEY together"
            )
        unknown = self.llm2_stage_set - STAGES
        if unknown:
            raise ValueError(f"ASE_LLM2_STAGES has unknown stage(s): {', '.join(sorted(unknown))}")
        return self

    @property
    def llm2_stage_set(self) -> frozenset[str]:
        return frozenset(s.strip() for s in self.llm2_stages.split(",") if s.strip())

    @property
    def llm_configured(self) -> bool:
        return self.llm_provider != "none"


@lru_cache
def get_settings() -> Settings:
    return Settings()
