"""Core domain models: findings, check results, and run records."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ase.core.states import RunState, assert_transition, is_terminal


def _now() -> datetime:
    return datetime.now(UTC)


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingStatus(StrEnum):
    UNVERIFIED = "unverified"  # reported, not yet checked against evidence
    CONFIRMED = "confirmed"  # backed by concrete evidence
    DISMISSED = "dismissed"  # reviewed and rejected


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNVERIFIED = "unverified"


class ExclusionReason(StrEnum):
    SENSITIVE = "sensitive"  # likely secrets; never sent to a model
    BINARY = "binary"
    OVERSIZED = "oversized"
    SYMLINK = "symlink"


class InventoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str  # relative, POSIX-style
    size_bytes: int = Field(ge=0)
    language: str | None = None
    excluded: ExclusionReason | None = None


class RepositoryInventory(BaseModel):
    """Facts observed by deterministic scanning. No model involved."""

    files: list[InventoryEntry] = Field(default_factory=list)
    total_files: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    language_bytes: dict[str, int] = Field(default_factory=dict)
    primary_language: str | None = None
    dependency_files: list[str] = Field(default_factory=list)
    test_files: list[str] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    # Build/deploy/CI configuration files that exist (Dockerfile, workflows, vercel.json, ...)
    devops_files: list[str] = Field(default_factory=list)
    # Hints only: derived from words in dependency files, not verified.
    framework_hints: list[str] = Field(default_factory=list)
    skipped_directories: list[str] = Field(default_factory=list)
    excluded_counts: dict[str, int] = Field(default_factory=dict)
    # Build ecosystems detected from manifest files, e.g. ["python", "node"]
    ecosystems: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_path: str = Field(min_length=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    snippet: str | None = Field(default=None, max_length=2_000)
    # True only if ASE itself confirmed that the cited file, lines (and quote, if any) exist.
    # This proves the code is really there, NOT that the model's interpretation is right.
    location_verified: bool = False

    @model_validator(mode="after")
    def _line_order(self) -> Evidence:
        if self.line_end is not None:
            if self.line_start is None:
                raise ValueError("line_end requires line_start")
            if self.line_end < self.line_start:
                raise ValueError("line_end must be >= line_start")
        return self


class Finding(BaseModel):
    id: str = Field(min_length=1)
    severity: Severity
    category: str = Field(min_length=1)
    description: str = Field(min_length=1)
    # "static" = observed by deterministic analysis; "llm" = model interpretation.
    source: Literal["static", "llm"]
    # "fix" = something wrong that must change; "addition" = something missing that should be added
    kind: Literal["fix", "addition"] = "fix"
    evidence: list[Evidence] = Field(default_factory=list)
    recommended_fix: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    status: FindingStatus = FindingStatus.UNVERIFIED

    @model_validator(mode="after")
    def _confirmed_needs_evidence(self) -> Finding:
        if self.status is FindingStatus.CONFIRMED and not self.evidence:
            raise ValueError("A confirmed finding must include evidence.")
        return self


class CheckResult(BaseModel):
    """One verification check (test run, lint run, ...) and what actually happened."""

    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    status: CheckStatus
    exit_code: int | None = None
    output: str = Field(default="", max_length=65_536)  # sanitized, bounded
    duration_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _passed_failed_need_exit_code(self) -> CheckResult:
        if self.status in {CheckStatus.PASSED, CheckStatus.FAILED} and self.exit_code is None:
            raise ValueError("passed/failed checks must record an observed exit code.")
        if self.status is CheckStatus.PASSED and self.exit_code != 0:
            raise ValueError("A passed check must have exit code 0.")
        return self


class AuditReport(BaseModel):
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    rejected_findings: int = Field(default=0, ge=0)
    rejection_reasons: list[str] = Field(default_factory=list)
    files_sent: list[str] = Field(default_factory=list)
    provider: str
    model: str
    prompt_version: str
    model_calls: int = Field(default=0, ge=0)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None  # provider-reported actual cost only; None if unknown
    generated_at: datetime = Field(default_factory=_now)
    limitations: list[str] = Field(default_factory=list)


class PlannedChange(BaseModel):
    """One change the build step will make. Validated by code, not trusted from the model."""

    id: str = Field(min_length=1)
    action: Literal["modify", "create"]
    path: str = Field(min_length=1)  # relative, POSIX-style, checked against the workspace
    summary: str = Field(min_length=1)
    finding_ids: list[str] = Field(min_length=1)  # every change is tied to audit findings
    verification: str = ""
    risk: Literal["low", "medium", "high"] = "medium"


class DesignDecision(BaseModel):
    decision: str = Field(min_length=1)
    rationale: str = ""


class DeferredFinding(BaseModel):
    finding_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Plan(BaseModel):
    architecture_summary: str = ""
    design_decisions: list[DesignDecision] = Field(default_factory=list)
    changes: list[PlannedChange] = Field(default_factory=list)
    deferred: list[DeferredFinding] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    test_strategy: str = ""
    rejected_changes: int = Field(default=0, ge=0)
    rejection_reasons: list[str] = Field(default_factory=list)
    files_sent: list[str] = Field(default_factory=list)
    provider: str
    model: str
    prompt_version: str
    model_calls: int = Field(default=0, ge=0)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    generated_at: datetime = Field(default_factory=_now)
    limitations: list[str] = Field(default_factory=list)


class BuiltChange(BaseModel):
    change_id: str
    path: str
    action: Literal["modify", "create"]
    status: Literal["applied", "failed", "skipped"]
    reason: str = ""  # why it failed or was skipped (ASE-generated text)
    diff: str = ""  # unified diff against the untouched clone, capped
    added_lines: int = Field(default=0, ge=0)
    removed_lines: int = Field(default=0, ge=0)
    # "passed"/"failed" only for formats ASE can parse itself (Python, JSON, TOML);
    # everything else is "not_checked", never "passed".
    syntax_check: Literal["passed", "failed", "not_checked"] = "not_checked"
    attempts: int = Field(default=0, ge=0)


class BuildReport(BaseModel):
    changes: list[BuiltChange] = Field(default_factory=list)
    applied: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    build_dir: str = ""  # relative, e.g. "build/<run id>"
    provider: str
    model: str
    prompt_version: str
    model_calls: int = Field(default=0, ge=0)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    generated_at: datetime = Field(default_factory=_now)
    limitations: list[str] = Field(default_factory=list)


class VerifyRound(BaseModel):
    index: int = Field(ge=1)
    checks: list[CheckResult] = Field(default_factory=list)
    outcome: Literal["passed", "failed", "unverified"]
    unverified_reasons: list[str] = Field(default_factory=list)
    repaired: list[BuiltChange] = Field(default_factory=list)  # changes made AFTER this round


class VerifyReport(BaseModel):
    rounds: list[VerifyRound] = Field(default_factory=list)
    # passed: tests really ran and passed. failed: something really failed.
    # unverified: nothing could be proven either way (never presented as success).
    outcome: Literal["passed", "failed", "unverified"]
    max_repair_attempts: int = Field(ge=0)
    repair_attempts_used: int = Field(default=0, ge=0)
    sandbox: str = ""
    network_policy: str = ""
    provider: str = ""
    model: str = ""
    model_calls: int = Field(default=0, ge=0)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    generated_at: datetime = Field(default_factory=_now)
    limitations: list[str] = Field(default_factory=list)


class StateChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_state: RunState
    to_state: RunState
    at: datetime = Field(default_factory=_now)
    reason: str = ""


class ActionRecord(BaseModel):
    name: str = Field(min_length=1)
    started_at: datetime = Field(default_factory=_now)
    ended_at: datetime | None = None
    exit_status: Literal["ok", "error", "blocked", "skipped"] = "ok"
    summary: str = ""  # sanitized; never raw secrets or hidden reasoning


class RunRecord(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    state: RunState = RunState.CREATED
    source: str | None = None  # human-readable origin, e.g. "github.com/owner/repo"
    created_at: datetime = Field(default_factory=_now)
    ended_at: datetime | None = None
    inventory: RepositoryInventory | None = None
    audit: AuditReport | None = None
    plan: Plan | None = None
    build: BuildReport | None = None
    verification: VerifyReport | None = None
    approved_scope: list[str] = Field(default_factory=list)  # relative paths
    history: list[StateChange] = Field(default_factory=list)
    actions: list[ActionRecord] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    final_status: str | None = None

    def transition(self, new_state: RunState, reason: str = "") -> None:
        """Move to ``new_state`` or raise ``InvalidTransitionError``."""
        assert_transition(self.state, new_state)
        self.history.append(StateChange(from_state=self.state, to_state=new_state, reason=reason))
        self.state = new_state
        if is_terminal(new_state):
            self.ended_at = _now()
            self.final_status = new_state.value
