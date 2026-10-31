"""Audit orchestration: select context, ask the model, validate, report."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ase.agent.evidence import validate_raw_audit
from ase.agent.llm_json import ask_for_json
from ase.core.config import Settings
from ase.core.models import AuditReport, RepositoryInventory
from ase.llm.base import LLMProvider
from ase.llm.parsing import parse_audit_json
from ase.llm.prompts.audit import PROMPT_VERSION, build_audit_request
from ase.llm.retry import ModelCallBudget
from ase.llm.schemas import ContextFile
from ase.repository.context import DEFAULT_MAX_BYTES, select_context_files

AUDIT_LIMITATIONS = [
    "Findings are AI interpretations. ASE verified that cited code exists at the stated "
    "location, not that the interpretation is correct.",
    "Only the files listed as sent to the model were examined.",
]


@dataclass(frozen=True)
class AuditLimits:
    max_model_calls: int
    max_context_files: int
    max_context_bytes: int
    parse_attempts: int = 2
    transient_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_file_bytes: int = DEFAULT_MAX_BYTES
    max_output_tokens: int = 8_000

    @classmethod
    def from_settings(cls, s: Settings) -> AuditLimits:
        return cls(
            s.max_model_calls,
            s.max_context_files,
            s.max_context_bytes,
            max_output_tokens=s.llm_max_output_tokens,
        )


class Auditor:
    def __init__(
        self,
        provider: LLMProvider,
        limits: AuditLimits,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._provider = provider
        self._limits = limits
        self._sleep = sleep

    def audit(self, workspace: Path | str, inventory: RepositoryInventory) -> AuditReport:
        lim = self._limits
        files = select_context_files(
            workspace,
            inventory,
            max_files=lim.max_context_files,
            max_total_bytes=lim.max_context_bytes,
            max_file_bytes=lim.max_file_bytes,
        )

        def report(model: str = "n/a", **fields: Any) -> AuditReport:
            return AuditReport(
                provider=self._provider.name,
                model=model,
                prompt_version=PROMPT_VERSION,
                files_sent=[p for p, _ in files],
                limitations=list(AUDIT_LIMITATIONS),
                **fields,
            )

        if not files:
            return report(summary="No analyzable files were available to send to the model.")

        request = build_audit_request(
            [ContextFile(path=p, content=t) for p, t in files], lim.max_output_tokens
        )
        raw, usage = ask_for_json(
            self._provider,
            request,
            parse_audit_json,
            budget=ModelCallBudget(lim.max_model_calls),
            parse_attempts=lim.parse_attempts,
            transient_attempts=lim.transient_attempts,
            base_delay=lim.base_delay_seconds,
            sleep=self._sleep,
        )
        findings, reasons = validate_raw_audit(
            raw, workspace, {p for p, _ in files}, lim.max_file_bytes
        )
        return report(
            model=usage.model,
            summary=raw.summary.strip()[:2_000],
            findings=findings,
            rejected_findings=len(reasons),
            rejection_reasons=sorted(set(reasons)),
            model_calls=usage.model_calls,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_usd=usage.cost_usd,
        )
