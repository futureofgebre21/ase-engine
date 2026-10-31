"""Turn an untrusted model plan into a plan ASE can stand behind.

Every rule here is deterministic code. The model proposes; this module decides.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from ase.core.models import (
    DeferredFinding,
    DesignDecision,
    Finding,
    PlannedChange,
    RepositoryInventory,
)
from ase.llm.schemas import RawPlan
from ase.repository.context import is_noise_file
from ase.repository.inventory import BINARY_EXT, SKIP_DIRS
from ase.security.redaction import is_sensitive_path, redact

MAX_PATH_CHARS = 200
_TEXT_CAP = 1_500
_MAX_DECISIONS = 10
_MAX_RISKS = 10
_DRIVE = re.compile(r"^[A-Za-z]:")
UNADDRESSED_REASON = "Not addressed by the plan."


@dataclass
class ValidatedPlan:
    architecture_summary: str = ""
    design_decisions: list[DesignDecision] = field(default_factory=list)
    changes: list[PlannedChange] = field(default_factory=list)
    deferred: list[DeferredFinding] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    test_strategy: str = ""
    rejection_reasons: list[str] = field(default_factory=list)


def normalize_plan_path(raw: object) -> str | None:
    """Return a clean relative POSIX path, or ``None`` if it is unsafe or malformed."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) > MAX_PATH_CHARS or "\x00" in text or "\\" in text:
        return None
    if _DRIVE.match(text):
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return None
    parts = [part for part in path.parts if part != "."]
    return "/".join(parts) if parts else None


def is_forbidden_target(path: str) -> bool:
    """Files the build step must never create or modify."""
    p = PurePosixPath(path)
    return (
        any(part in SKIP_DIRS for part in p.parts)
        or is_sensitive_path(path)
        or is_noise_file(p.name)
        or p.suffix.lower() in BINARY_EXT
    )


def _clean(text: str, cap: int = _TEXT_CAP) -> str:
    return redact(text).strip()[:cap]


def validate_raw_plan(
    raw: RawPlan,
    *,
    inventory: RepositoryInventory,
    findings: list[Finding],
    sent_paths: Collection[str],
    max_changes: int,
) -> ValidatedPlan:
    out = ValidatedPlan(
        architecture_summary=_clean(raw.architecture_summary, 4_000),
        test_strategy=_clean(raw.test_strategy),
        risks=[r for r in (_clean(x, 500) for x in raw.risks[:_MAX_RISKS]) if r],
    )
    out.design_decisions = [
        DesignDecision(decision=d, rationale=_clean(x.rationale, 800))
        for x in raw.design_decisions[:_MAX_DECISIONS]
        if (d := _clean(x.decision, 500))
    ]

    known_ids = {f.id for f in findings}
    existing = {e.path.lower() for e in inventory.files}
    existing_files = {e.path for e in inventory.files}
    seen: set[str] = set()

    for rc in raw.changes:
        action_text = rc.action.strip().lower()
        if action_text not in ("modify", "create"):
            out.rejection_reasons.append("invalid action")
            continue
        action: Literal["modify", "create"] = "modify" if action_text == "modify" else "create"
        path = normalize_plan_path(rc.path)
        if path is None:
            out.rejection_reasons.append("unsafe or malformed path")
            continue
        if is_forbidden_target(path):
            out.rejection_reasons.append("forbidden target (secret, generated, binary or .git)")
            continue
        if path.lower() in seen:
            out.rejection_reasons.append("duplicate path")
            continue
        if action == "modify" and path not in sent_paths:
            out.rejection_reasons.append("modify target does not exist or was not shown")
            continue
        if action == "create":
            parents = {"/".join(PurePosixPath(path).parts[:i]) for i in range(1, 10)}
            if path.lower() in existing or parents & existing_files:
                out.rejection_reasons.append("create target already exists (use modify)")
                continue
        ids = list(dict.fromkeys(i for i in rc.finding_ids if i in known_ids))
        if not ids:
            out.rejection_reasons.append("change is not tied to a known finding")
            continue
        summary = _clean(rc.summary)
        if not summary:
            out.rejection_reasons.append("missing summary")
            continue
        if len(out.changes) >= max_changes:
            out.rejection_reasons.append(f"beyond the limit of {max_changes} changes")
            continue
        seen.add(path.lower())
        risk = rc.risk.strip().lower()
        out.changes.append(
            PlannedChange(
                id=f"C-{len(out.changes) + 1:03d}",  # assigned by ASE, never by the model
                action=action,
                path=path,
                summary=summary,
                finding_ids=ids,
                verification=_clean(rc.verification, 800),
                risk=risk if risk in ("low", "medium", "high") else "medium",
            )
        )

    # Complete accounting: every finding is either planned, deferred with a reason, or flagged.
    covered = {i for c in out.changes for i in c.finding_ids}
    for d in raw.deferred:
        if d.finding_id in known_ids and d.finding_id not in covered:
            if d.finding_id not in {x.finding_id for x in out.deferred}:
                out.deferred.append(
                    DeferredFinding(
                        finding_id=d.finding_id, reason=_clean(d.reason, 600) or UNADDRESSED_REASON
                    )
                )
    handled = covered | {d.finding_id for d in out.deferred}
    for f in findings:
        if f.id not in handled:
            out.deferred.append(DeferredFinding(finding_id=f.id, reason=UNADDRESSED_REASON))
    return out
