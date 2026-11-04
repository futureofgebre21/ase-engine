"""Bounded repair of failing checks. Edits are limited to the plan's approved files."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from ase.agent.builder import write_atomic
from ase.agent.edits import apply_edits, decode_text, encode_text, syntax_check, unified_diff
from ase.agent.llm_json import Usage, ask_for_json
from ase.agent.plan_validation import is_forbidden_target, normalize_plan_path
from ase.core.config import Settings
from ase.core.errors import AseError, ChangeRejected, PathSafetyError
from ase.core.models import BuiltChange, CheckResult, CheckStatus, Plan
from ase.llm.base import LLMProvider
from ase.llm.parsing import parse_json_model
from ase.llm.prompts.repair import (
    OUTPUT_PATH,
    PLAN_PATH,
    PROMPT_VERSION,
    build_repair_request,
)
from ase.llm.retry import ModelCallBudget
from ase.llm.schemas import ContextFile, RawFileEdits, RawRepair
from ase.repository.context import read_file_for_model
from ase.security.paths import resolve_within
from ase.security.redaction import redact

_MAX_FAILURE_CHARS = 6_000
_MAX_FILES = 8


@dataclass(frozen=True)
class RepairLimits:
    max_context_bytes: int
    max_generated_file_bytes: int
    max_output_tokens: int = 8_000
    max_target_bytes: int = 100_000
    parse_attempts: int = 2
    transient_attempts: int = 3
    base_delay_seconds: float = 1.0

    @classmethod
    def from_settings(cls, s: Settings) -> RepairLimits:
        return cls(
            s.max_context_bytes,
            s.max_generated_file_bytes,
            max_output_tokens=s.llm_max_output_tokens,
        )


@dataclass
class RepairOutcome:
    changes: list[BuiltChange] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    prompt_version: str = PROMPT_VERSION

    @property
    def applied(self) -> int:
        return sum(1 for c in self.changes if c.status == "applied")


def _failure_text(failing: list[CheckResult]) -> str:
    parts = []
    for check in failing:
        tail = redact(check.output)[-_MAX_FAILURE_CHARS:]
        parts.append(f"== {check.name}: {check.command} (exit code {check.exit_code}) ==\n{tail}")
    return "\n\n".join(parts)


class Repairer:
    def __init__(
        self,
        provider: LLMProvider,
        limits: RepairLimits,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._provider = provider
        self._limits = limits
        self._sleep = sleep

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def repair(
        self,
        *,
        build_dir: Path,
        plan: Plan,
        failing: list[CheckResult],
        attempt: int,
        budget: ModelCallBudget,
    ) -> RepairOutcome:
        lim = self._limits
        scope = [
            c.path
            for c in plan.changes
            if (build_dir / c.path).is_file() and not (build_dir / c.path).is_symlink()
        ][:_MAX_FILES]
        if not scope:
            return RepairOutcome()

        context: list[ContextFile] = []
        total = 0
        for path in scope:
            try:
                text = read_file_for_model(build_dir, path, lim.max_target_bytes)
            except (AseError, OSError):
                continue
            if total + len(text) > lim.max_context_bytes:
                continue
            total += len(text)
            context.append(ContextFile(path=path, content=text, numbered=False))
        if not context:
            return RepairOutcome()
        context.append(
            ContextFile(path=OUTPUT_PATH, content=_failure_text(failing), numbered=False)
        )
        summary = {
            "architecture_summary": plan.architecture_summary,
            "test_strategy": plan.test_strategy,
            "changes": [
                {"id": c.id, "path": c.path, "summary": c.summary, "verification": c.verification}
                for c in plan.changes
            ],
        }
        context.append(
            ContextFile(path=PLAN_PATH, content=json.dumps(summary, indent=2), numbered=False)
        )

        request = build_repair_request(context, attempt, lim.max_output_tokens)
        raw, usage = ask_for_json(
            self._provider,
            request,
            partial(parse_json_model, model=RawRepair),
            budget=budget,
            parse_attempts=lim.parse_attempts,
            transient_attempts=lim.transient_attempts,
            base_delay=lim.base_delay_seconds,
            sleep=self._sleep,
        )
        allowed = {c.path for c in context if c.path not in (OUTPUT_PATH, PLAN_PATH)}
        changes = [
            self._apply(build_dir, attempt, index, entry, allowed)
            for index, entry in enumerate(raw.files[:_MAX_FILES], start=1)
        ]
        return RepairOutcome(changes=changes, usage=usage)

    def _apply(
        self, build_dir: Path, attempt: int, index: int, entry: RawFileEdits, allowed: set[str]
    ) -> BuiltChange:
        change_id = f"R{attempt}-{index}"
        path = normalize_plan_path(entry.path)

        def fail(reason: str) -> BuiltChange:
            return BuiltChange(
                change_id=change_id, path=path or "?", action="modify", status="failed",
                reason=reason, attempts=1,
            )  # fmt: skip

        if path is None or is_forbidden_target(path) or path not in allowed:
            return fail("The file is outside the approved scope of this run.")
        try:
            target = resolve_within(build_dir, path)
            text, newline, bom = decode_text(target.read_bytes())
            new_text = apply_edits(
                text, entry.edits, max_bytes=self._limits.max_generated_file_bytes
            )
            status, detail = syntax_check(path, new_text)
            if status == "failed":
                raise ChangeRejected(f"the repaired code has a syntax error: {detail}")
            write_atomic(build_dir, path, encode_text(new_text, newline, bom))
        except (ChangeRejected, PathSafetyError, OSError) as exc:
            return fail(getattr(exc, "message", "The file could not be edited."))
        diff, added, removed = unified_diff(path, text, new_text)
        return BuiltChange(
            change_id=change_id, path=path, action="modify", status="applied", diff=diff,
            added_lines=added, removed_lines=removed, syntax_check=status, attempts=1,
        )  # fmt: skip


def failing_checks(checks: list[CheckResult]) -> list[CheckResult]:
    return [c for c in checks if c.status is CheckStatus.FAILED]
