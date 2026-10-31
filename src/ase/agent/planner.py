"""Step 3: turn verified audit findings into a validated architecture and change plan."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ase.agent.llm_json import ask_for_json
from ase.agent.plan_validation import validate_raw_plan
from ase.core.config import Settings
from ase.core.models import Finding, Plan, RepositoryInventory
from ase.llm.base import LLMProvider
from ase.llm.parsing import parse_plan_json
from ase.llm.prompts.plan import FINDINGS_PATH, PROMPT_VERSION, build_plan_request
from ase.llm.retry import ModelCallBudget
from ase.llm.schemas import ContextFile
from ase.repository.context import DEFAULT_MAX_BYTES, select_context_files

PLAN_LIMITATIONS = [
    "The plan is an AI proposal. ASE verified its structure (paths, finding links, no secrets "
    "or generated files), not that the design is the best one.",
    "No file has been changed yet; the build step writes changes later.",
]


@dataclass(frozen=True)
class PlanLimits:
    max_model_calls: int
    max_context_files: int
    max_context_bytes: int
    max_changes: int
    max_output_tokens: int = 8_000
    max_file_bytes: int = DEFAULT_MAX_BYTES
    parse_attempts: int = 2
    transient_attempts: int = 3
    base_delay_seconds: float = 1.0

    @classmethod
    def from_settings(cls, s: Settings) -> PlanLimits:
        return cls(
            s.max_model_calls,
            s.max_context_files,
            s.max_context_bytes,
            s.max_plan_changes,
            max_output_tokens=s.llm_max_output_tokens,
        )


def _findings_json(findings: list[Finding]) -> str:
    """The audit findings as data for the model (already verified and redacted)."""
    items = [
        {
            "id": f.id,
            "severity": f.severity.value,
            "kind": f.kind,
            "category": f.category,
            "description": f.description,
            "evidence": [
                {"file_path": e.file_path, "line_start": e.line_start, "line_end": e.line_end}
                for e in f.evidence
            ],
            "recommended_fix": f.recommended_fix,
        }
        for f in findings
    ]
    return json.dumps(items, indent=2, ensure_ascii=False)


class Planner:
    def __init__(
        self,
        provider: LLMProvider,
        limits: PlanLimits,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._provider = provider
        self._limits = limits
        self._sleep = sleep

    def plan(
        self, workspace: Path | str, inventory: RepositoryInventory, findings: list[Finding]
    ) -> Plan:
        lim = self._limits
        files = select_context_files(
            workspace,
            inventory,
            max_files=lim.max_context_files,
            max_total_bytes=lim.max_context_bytes,
            max_file_bytes=lim.max_file_bytes,
        )
        context = [ContextFile(path=p, content=t) for p, t in files]
        context.append(ContextFile(path=FINDINGS_PATH, content=_findings_json(findings)))
        request = build_plan_request(context, lim.max_changes, lim.max_output_tokens)

        raw, usage = ask_for_json(
            self._provider,
            request,
            parse_plan_json,
            budget=ModelCallBudget(lim.max_model_calls),
            parse_attempts=lim.parse_attempts,
            transient_attempts=lim.transient_attempts,
            base_delay=lim.base_delay_seconds,
            sleep=self._sleep,
        )
        checked = validate_raw_plan(
            raw,
            inventory=inventory,
            findings=findings,
            sent_paths={p for p, _ in files},
            max_changes=lim.max_changes,
        )
        return Plan(
            architecture_summary=checked.architecture_summary,
            design_decisions=checked.design_decisions,
            changes=checked.changes,
            deferred=checked.deferred,
            risks=checked.risks,
            test_strategy=checked.test_strategy,
            rejected_changes=len(checked.rejection_reasons),
            rejection_reasons=sorted(set(checked.rejection_reasons)),
            files_sent=[p for p, _ in files],
            provider=self._provider.name,
            model=usage.model,
            prompt_version=PROMPT_VERSION,
            model_calls=usage.model_calls,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_usd=usage.cost_usd,
            limitations=list(PLAN_LIMITATIONS),
        )
