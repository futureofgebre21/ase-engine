"""Typed request/response models shared by every provider adapter."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContextFile(BaseModel):
    """Repository file content. UNTRUSTED data; already redacted and size-bounded."""

    path: str
    content: str
    numbered: bool = True  # line-number margin (audit/plan); off when exact text matters


class LLMRequest(BaseModel):
    system: str  # our instructions (trusted)
    task: str  # what the user wants (trusted)
    context_files: list[ContextFile] = Field(default_factory=list)  # repo evidence (untrusted)
    prompt_version: str
    max_output_tokens: int = Field(default=2_000, gt=0)


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None  # actual provider-reported cost; never an estimate


class RawEvidence(BaseModel):
    """What the model CLAIMS. Nothing here is trusted until validated."""

    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    quote: str | None = None


class RawFinding(BaseModel):
    severity: str
    kind: str = "fix"
    category: str
    description: str
    evidence: list[RawEvidence] = Field(default_factory=list)
    recommended_fix: str | None = None
    confidence: float = 0.5


class RawAudit(BaseModel):
    summary: str = ""
    findings: list[RawFinding] = Field(default_factory=list)


class RawChange(BaseModel):
    action: str
    path: str
    summary: str
    finding_ids: list[str] = Field(default_factory=list)
    verification: str = ""
    risk: str = "medium"


class RawDecision(BaseModel):
    decision: str
    rationale: str = ""


class RawDeferred(BaseModel):
    finding_id: str
    reason: str = ""


class RawPlan(BaseModel):
    architecture_summary: str = ""
    design_decisions: list[RawDecision] = Field(default_factory=list)
    changes: list[RawChange] = Field(default_factory=list)
    deferred: list[RawDeferred] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    test_strategy: str = ""


class RawEdit(BaseModel):
    old: str
    new: str


class RawModifyResult(BaseModel):
    edits: list[RawEdit] = Field(default_factory=list)
    notes: str = ""


class RawCreateResult(BaseModel):
    content: str
    notes: str = ""
