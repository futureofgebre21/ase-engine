"""Turn untrusted model claims into findings ASE can stand behind.

A finding is kept only if at least one cited location is verified against the real file
(exact text the model saw). Verification proves the code EXISTS where claimed. It does not
prove the model's interpretation is correct, so findings stay ``UNVERIFIED``; ``CONFIRMED`` is
reserved for deterministic confirmation (for example a failing test) in later phases.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from pathlib import Path

from ase.core.errors import AseError
from ase.core.models import Evidence, Finding, FindingStatus, Severity
from ase.llm.schemas import RawAudit, RawEvidence
from ase.repository.context import MAX_SPAN_LINES, read_file_for_model
from ase.security.redaction import redact

MAX_FINDINGS = 30
_TEXT_CAP = 2_000


def _norm(s: str) -> str:
    return " ".join(s.split())


def _verify_evidence(
    raw: RawEvidence, workspace: Path | str, allowed: Collection[str], max_bytes: int
) -> Evidence | None:
    if raw.file_path not in allowed:  # also blocks sensitive files and paths never sent
        return None
    try:
        lines = read_file_for_model(workspace, raw.file_path, max_bytes).splitlines()
    except AseError:
        return None

    start, end = raw.line_start, raw.line_end
    quote = _norm(raw.quote) if raw.quote else ""

    if start is None and end is None:
        if not quote:
            return None  # file-level claim with nothing checkable
        hits = [i for i in range(len(lines)) if quote in _norm(lines[i])]
        if len(hits) != 1:
            return None  # not found, or ambiguous
        start = end = hits[0] + 1
    else:
        if start is None:
            return None
        end = start if end is None else end
        if not (1 <= start <= end <= len(lines)) or end - start + 1 > MAX_SPAN_LINES:
            return None
        if quote and quote not in _norm(" ".join(lines[start - 1 : end])):
            return None

    return Evidence(
        file_path=raw.file_path,
        line_start=start,
        line_end=end,
        snippet="\n".join(lines[start - 1 : end])[:_TEXT_CAP],
        location_verified=True,
    )


def validate_raw_audit(
    raw: RawAudit, workspace: Path | str, allowed: Collection[str], max_bytes: int
) -> tuple[list[Finding], list[str]]:
    """Return ``(findings, rejection_reasons)``."""
    findings: list[Finding] = []
    reasons: list[str] = []
    seen: set[tuple[str, str]] = set()

    candidates = raw.findings
    if len(candidates) > MAX_FINDINGS:
        extra = len(candidates) - MAX_FINDINGS
        reasons.append(f"{extra} finding(s) beyond the limit of {MAX_FINDINGS}")
        candidates = candidates[:MAX_FINDINGS]

    for rf in candidates:
        try:
            severity = Severity(rf.severity.strip().lower())
        except ValueError:
            reasons.append("invalid severity")
            continue
        category = redact(rf.category).strip()[:100]
        description = redact(rf.description).strip()[:_TEXT_CAP]
        if not category or not description:
            reasons.append("missing category or description")
            continue
        kind = "addition" if rf.kind.strip().lower() == "addition" else "fix"
        key = (category.lower(), description.lower())
        if key in seen:
            reasons.append("duplicate finding")
            continue
        verified = [
            ev
            for r in rf.evidence
            if (ev := _verify_evidence(r, workspace, allowed, max_bytes)) is not None
        ]
        if not verified:
            reasons.append("no verifiable evidence (cited file/lines/quote not found)")
            continue
        seen.add(key)
        conf = rf.confidence
        conf = 0.5 if (isinstance(conf, float) and math.isnan(conf)) else min(1.0, max(0.0, conf))
        fix = redact(rf.recommended_fix).strip()[:_TEXT_CAP] if rf.recommended_fix else None
        findings.append(
            Finding(
                id=f"F-{len(findings) + 1:03d}",  # assigned by ASE, never by the model
                severity=severity,
                category=category,
                description=description,
                source="llm",
                kind=kind,
                evidence=verified,
                recommended_fix=fix or None,
                confidence=conf,
                status=FindingStatus.UNVERIFIED,
            )
        )
    return findings, reasons
