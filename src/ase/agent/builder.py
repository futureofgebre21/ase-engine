"""Step 4: implement the plan inside the isolated build copy, one change at a time.

The model proposes text. Deterministic code decides whether it is applied:
paths are re-validated, edits must match the real file exactly once, generated text may not
contain secrets, parseable formats must parse, and every write stays inside the build copy.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from functools import partial
from dataclasses import dataclass
from pathlib import Path

from ase.agent.edits import (
    apply_edits,
    check_generated_text,
    decode_text,
    encode_text,
    syntax_check,
    unified_diff,
)
from ase.agent.llm_json import Usage, ask_for_json
from ase.agent.plan_validation import is_forbidden_target, normalize_plan_path
from ase.core.config import Settings
from ase.core.errors import (
    AseError,
    BudgetExceededError,
    ChangeRejected,
    ConfigurationError,
    LLMRequestError,
    LLMResponseError,
    LLMTransientError,
    PathSafetyError,
)
from ase.core.models import (
    BuildReport,
    BuiltChange,
    Finding,
    Plan,
    PlannedChange,
    RepositoryInventory,
)
from ase.llm.base import LLMProvider
from ase.llm.parsing import parse_json_model
from ase.llm.prompts.build import CHANGE_PATH, PROMPT_VERSION, build_change_request
from ase.llm.retry import ModelCallBudget
from ase.llm.schemas import ContextFile, RawCreateResult, RawModifyResult
from ase.repository.context import read_file_for_model
from ase.security.paths import resolve_within
from ase.security.redaction import redact

BUILD_LIMITATIONS = [
    "The code was written by an AI model and has NOT been run or verified yet. "
    "Only syntax of Python, JSON and TOML files was checked by ASE.",
    "All changes are in an isolated copy. The original clone and the remote repository are "
    "untouched.",
]
_TRUNCATED = "\n[truncated]"
_MAX_CITED_FILES = 3
_MAX_RECENT_FILES = 3


@dataclass(frozen=True)
class BuildLimits:
    max_model_calls: int
    max_context_bytes: int
    max_generated_file_bytes: int
    max_output_tokens: int = 8_000
    max_target_bytes: int = 100_000  # largest existing file ASE will edit
    max_other_file_bytes: int = 20_000
    attempts_per_change: int = 2
    parse_attempts: int = 2
    transient_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_consecutive_server_failures: int = 2

    @classmethod
    def from_settings(cls, s: Settings) -> BuildLimits:
        return cls(
            s.max_build_model_calls,
            s.max_context_bytes,
            s.max_generated_file_bytes,
            max_output_tokens=s.llm_max_output_tokens,
        )


def write_atomic(build_dir: Path, rel: str, data: bytes) -> None:
    """Write inside ``build_dir`` only; ``resolve_within`` rejects anything that would escape."""
    target = resolve_within(build_dir, rel)  # raises PathSafetyError if it would escape
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".ase-tmp")
    temp.write_bytes(data)
    os.replace(temp, target)


class Builder:
    def __init__(
        self,
        provider: LLMProvider,
        limits: BuildLimits,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._provider = provider
        self._limits = limits
        self._sleep = sleep

    # ------------------------------------------------------------------ public

    def build(
        self,
        *,
        build_dir: Path,
        build_name: str,
        plan: Plan,
        findings: list[Finding],
        inventory: RepositoryInventory,
        progress: Callable[[str], None] | None = None,
    ) -> BuildReport:
        """Apply every planned change it can. Never raises for one bad change; returns the truth."""
        say = progress or (lambda message: None)
        budget = ModelCallBudget(self._limits.max_model_calls)
        usage = Usage()
        by_id = {f.id: f for f in findings}
        recent: list[tuple[str, str]] = []  # (path, new text) of files already written
        results: list[BuiltChange] = []
        server_failures = 0
        stop_reason = ""

        for change in plan.changes:
            if stop_reason:
                results.append(self._skipped(change, stop_reason))
                continue
            say(f"{change.id}: {change.action} {change.path}")
            try:
                built, new_text = self._build_one(
                    change, build_dir, by_id, inventory, recent, budget, usage, plan
                )
            except BudgetExceededError:
                stop_reason = "The model call limit for this build was reached."
                results.append(self._skipped(change, stop_reason))
                continue
            except ConfigurationError:
                raise  # for example a rejected API key: every further call would fail too
            except (LLMTransientError, LLMRequestError) as exc:
                server_failures += 1
                results.append(self._failed(change, f"The AI server failed: {exc.message}"))
                if server_failures >= self._limits.max_consecutive_server_failures:
                    stop_reason = "The AI server kept failing, so the build was stopped."
                continue
            except LLMResponseError as exc:
                server_failures = 0
                results.append(self._failed(change, exc.message))
                continue
            server_failures = 0
            results.append(built)
            if new_text is not None:
                recent.append((change.path, new_text))

        applied = sum(1 for r in results if r.status == "applied")
        failed = sum(1 for r in results if r.status == "failed")
        return BuildReport(
            changes=results,
            applied=applied,
            failed=failed,
            skipped=len(results) - applied - failed,
            build_dir=build_name,
            provider=self._provider.name,
            model=usage.model,
            prompt_version=PROMPT_VERSION,
            model_calls=budget.used,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_usd=usage.cost_usd,
            limitations=list(BUILD_LIMITATIONS),
        )

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _failed(change: PlannedChange, reason: str, attempts: int = 0) -> BuiltChange:
        return BuiltChange(
            change_id=change.id, path=change.path, action=change.action, status="failed",
            reason=redact(reason)[:600], attempts=attempts,
        )  # fmt: skip

    @staticmethod
    def _skipped(change: PlannedChange, reason: str) -> BuiltChange:
        return BuiltChange(
            change_id=change.id, path=change.path, action=change.action, status="skipped",
            reason=reason,
        )  # fmt: skip

    def _build_one(
        self,
        change: PlannedChange,
        build_dir: Path,
        findings: dict[str, Finding],
        inventory: RepositoryInventory,
        recent: list[tuple[str, str]],
        budget: ModelCallBudget,
        usage: Usage,
        plan: Plan,
    ) -> tuple[BuiltChange, str | None]:
        lim = self._limits
        # Defence in depth: the plan was validated, but this step re-checks before any write.
        path = normalize_plan_path(change.path)
        if path is None or path != change.path or is_forbidden_target(path):
            return self._failed(change, "The target path is not allowed."), None

        feedback = ""
        last_reason = "No attempt was made."
        for attempt in range(1, lim.attempts_per_change + 1):
            try:
                original = self._read_original(change, build_dir)
                context = self._context(change, build_dir, findings, inventory, recent, plan)
            except ChangeRejected as exc:
                return self._failed(change, exc.message, attempt - 1), None
            request = build_change_request(
                action=change.action,
                path=change.path,
                context=context,
                max_output_tokens=lim.max_output_tokens,
                feedback=feedback,
            )
            model_cls = RawModifyResult if change.action == "modify" else RawCreateResult
            raw, used = ask_for_json(
                self._provider,
                request,
                partial(parse_json_model, model=model_cls),
                budget=budget,
                parse_attempts=lim.parse_attempts,
                transient_attempts=lim.transient_attempts,
                base_delay=lim.base_delay_seconds,
                sleep=self._sleep,
            )
            usage.merge(used)
            try:
                new_text = self._new_text(change, original, raw)
                status, detail = syntax_check(change.path, new_text)
                if status == "failed":
                    raise ChangeRejected(f"the generated code has a syntax error: {detail}")
                old_text, newline, bom = original if original else (None, "\n", False)
                write_atomic(build_dir, change.path, encode_text(new_text, newline, bom))
            except PathSafetyError:
                return self._failed(change, "The target path is not allowed.", attempt), None
            except ChangeRejected as exc:
                last_reason = exc.message
                feedback = f"Reason: {exc.message}. Fix this and reply again."
                continue
            diff, added, removed = unified_diff(change.path, old_text, new_text)
            return (
                BuiltChange(
                    change_id=change.id, path=change.path, action=change.action,
                    status="applied", diff=diff, added_lines=added, removed_lines=removed,
                    syntax_check=status, attempts=attempt,
                ),
                new_text,
            )  # fmt: skip
        return self._failed(change, last_reason, lim.attempts_per_change), None

    def _read_original(
        self, change: PlannedChange, build_dir: Path
    ) -> tuple[str, str, bool] | None:
        """``(text, newline, bom)`` of the current file, or ``None`` for a new file."""
        target = resolve_within(build_dir, change.path)
        if change.action == "create":
            if target.exists():
                raise ChangeRejected("the file to create already exists")
            return None
        if not target.is_file() or target.is_symlink():
            raise ChangeRejected("the file to modify does not exist")
        if target.stat().st_size > self._limits.max_target_bytes:
            raise ChangeRejected("the file is too large to edit automatically")
        return decode_text(target.read_bytes())

    def _new_text(
        self,
        change: PlannedChange,
        original: tuple[str, str, bool] | None,
        raw: RawModifyResult | RawCreateResult,
    ) -> str:
        limit = self._limits.max_generated_file_bytes
        if isinstance(raw, RawModifyResult):
            if original is None:
                raise ChangeRejected("a create change must reply with file content")
            return apply_edits(original[0], raw.edits, max_bytes=limit)
        if original is not None:
            raise ChangeRejected("a modify change must reply with edits")
        content = raw.content.replace("\r\n", "\n")
        if not content.strip():
            raise ChangeRejected("the generated file is empty")
        check_generated_text(content, max_bytes=limit)
        return content if content.endswith("\n") else content + "\n"

    def _context(
        self,
        change: PlannedChange,
        build_dir: Path,
        findings: dict[str, Finding],
        inventory: RepositoryInventory,
        recent: list[tuple[str, str]],
        plan: Plan,
    ) -> list[ContextFile]:
        lim = self._limits
        files: list[ContextFile] = []
        seen = {change.path}
        total = 0

        def add(path: str, limit: int) -> bool:
            nonlocal total
            if path in seen or total >= lim.max_context_bytes:
                return False
            try:
                text = read_file_for_model(build_dir, path, limit)
            except (AseError, OSError):
                return False
            if total + len(text) > lim.max_context_bytes:
                return False
            seen.add(path)
            total += len(text)
            files.append(ContextFile(path=path, content=text, numbered=False))
            return True

        if change.action == "modify":
            try:
                text = read_file_for_model(build_dir, change.path, lim.max_target_bytes)
            except (AseError, OSError):
                raise ChangeRejected("the file to modify could not be read") from None
            if text.endswith(_TRUNCATED):
                raise ChangeRejected("the file is too large to edit automatically")
            files.append(ContextFile(path=change.path, content=text, numbered=False))
            total += len(text)

        linked = [findings[i] for i in change.finding_ids if i in findings]
        cited = [e.file_path for f in linked for e in f.evidence]
        added_cited = 0
        for path in dict.fromkeys(cited):
            if added_cited >= _MAX_CITED_FILES:
                break
            added_cited += add(path, lim.max_other_file_bytes)
        for path in inventory.dependency_files[:2]:
            add(path, lim.max_other_file_bytes)
        for path, text in reversed(recent[-_MAX_RECENT_FILES:]):  # earlier changes of this build
            if path not in seen and total + len(text) <= lim.max_context_bytes:
                seen.add(path)
                total += len(text)
                files.append(ContextFile(path=path, content=text, numbered=False))

        info = {
            "change": {
                "id": change.id, "action": change.action, "path": change.path,
                "summary": change.summary, "verification": change.verification,
                "risk": change.risk,
            },
            "architecture_summary": plan.architecture_summary,
            "design_decisions": [
                {"decision": d.decision, "rationale": d.rationale} for d in plan.design_decisions
            ],
            "test_strategy": plan.test_strategy,
            "findings": [
                {
                    "id": f.id, "kind": f.kind, "category": f.category,
                    "description": f.description, "recommended_fix": f.recommended_fix,
                    "evidence": [
                        {"file_path": e.file_path, "line_start": e.line_start,
                         "line_end": e.line_end} for e in f.evidence
                    ],
                }
                for f in linked
            ],
        }  # fmt: skip
        files.append(
            ContextFile(
                path=CHANGE_PATH,
                content=json.dumps(info, indent=2, ensure_ascii=False),
                numbered=False,
            )
        )
        return files

