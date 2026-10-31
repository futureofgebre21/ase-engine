import shutil
from pathlib import Path

from ase.agent.evidence import MAX_FINDINGS, validate_raw_audit
from ase.core.models import FindingStatus, Severity
from ase.llm.schemas import RawAudit, RawEvidence, RawFinding

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"
ALLOWED = {"cart.py", "main.py", "tests/test_cart.py", "requirements.txt", "README.md"}
BUG_LINE = "return price + price * percent / 100"


def _ws(tmp_path):
    root = tmp_path / "repo"
    if not root.exists():
        shutil.copytree(FIXTURE, root)
    return root


def _finding(evidence, **kw):
    base = dict(severity="high", category="logic-error", description="discount is added",
                evidence=evidence, recommended_fix="subtract it", confidence=0.9)  # fmt: skip
    base.update(kw)
    return RawFinding(**base)


def _run(tmp_path, *findings):
    return validate_raw_audit(RawAudit(summary="s", findings=list(findings)), _ws(tmp_path),
                              ALLOWED, 20_000)  # fmt: skip


def test_real_evidence_is_kept_with_actual_snippet(tmp_path):
    ev = RawEvidence(file_path="cart.py", line_start=6, line_end=6, quote=BUG_LINE)
    findings, reasons = _run(tmp_path, _finding([ev]))
    assert reasons == [] and len(findings) == 1
    f = findings[0]
    assert f.id == "F-001" and f.source == "llm" and f.severity is Severity.HIGH
    assert f.status is FindingStatus.UNVERIFIED  # AI findings are never auto-confirmed
    assert f.evidence[0].location_verified and BUG_LINE in f.evidence[0].snippet


def test_quote_without_lines_is_located(tmp_path):
    ev = RawEvidence(file_path="cart.py", quote=BUG_LINE)
    findings, _ = _run(tmp_path, _finding([ev]))
    assert findings[0].evidence[0].line_start == 6


def test_hallucinated_evidence_is_rejected(tmp_path):
    bad = [
        RawEvidence(file_path="nope.py", line_start=1, line_end=1),  # file does not exist
        RawEvidence(file_path="cart.py", line_start=500, line_end=501),  # lines out of range
        RawEvidence(file_path="cart.py", line_start=6, line_end=6, quote="return 42"),  # wrong
        RawEvidence(file_path="cart.py"),  # nothing checkable
        RawEvidence(file_path="cart.py", quote="this text is not in the file"),
        RawEvidence(file_path="cart.py", line_start=7, line_end=6),  # inverted
        RawEvidence(file_path="cart.py", line_end=6),  # end without start
    ]
    for ev in bad:
        findings, reasons = _run(tmp_path, _finding([ev]))
        assert findings == [] and len(reasons) == 1, ev


def test_files_never_sent_to_the_model_cannot_be_cited(tmp_path):
    for path in (".env.production", "logo.png", "../../etc/passwd", "/etc/passwd"):
        ev = RawEvidence(file_path=path, line_start=1, line_end=1)
        findings, _ = _run(tmp_path, _finding([ev]))
        assert findings == [], path


def test_one_good_evidence_among_bad_keeps_only_the_good(tmp_path):
    good = RawEvidence(file_path="cart.py", line_start=6, line_end=6)
    bad = RawEvidence(file_path="ghost.py", line_start=1, line_end=1)
    findings, _ = _run(tmp_path, _finding([bad, good]))
    assert [e.file_path for e in findings[0].evidence] == ["cart.py"]


def test_invalid_severity_empty_text_and_duplicates_are_rejected(tmp_path):
    ev = RawEvidence(file_path="cart.py", line_start=6, line_end=6)
    findings, reasons = _run(
        tmp_path,
        _finding([ev], severity="catastrophic"),
        _finding([ev], description="   "),
        _finding([ev]),
        _finding([ev]),  # duplicate of the previous one
    )
    assert len(findings) == 1 and len(reasons) == 3


def test_ids_are_assigned_by_ase_and_confidence_is_clamped(tmp_path):
    ev = RawEvidence(file_path="cart.py", line_start=6, line_end=6)
    findings, _ = _run(
        tmp_path,
        _finding([ev], description="a", confidence=7.0),
        _finding([ev], description="b", confidence=-3.0),
    )
    assert [f.id for f in findings] == ["F-001", "F-002"]
    assert [f.confidence for f in findings] == [1.0, 0.0]


def test_secrets_in_model_text_are_redacted(tmp_path):
    ev = RawEvidence(file_path="cart.py", line_start=6, line_end=6)
    secret = "ghp_" + "a" * 36
    findings, _ = _run(tmp_path, _finding([ev], description=f"leaks {secret}",
                                          recommended_fix=f"rotate {secret}"))  # fmt: skip
    assert secret not in findings[0].description and secret not in findings[0].recommended_fix


def test_finding_count_is_capped(tmp_path):
    ev = RawEvidence(file_path="cart.py", line_start=6, line_end=6)
    many = [_finding([ev], description=f"issue {i}") for i in range(MAX_FINDINGS + 5)]
    findings, reasons = _run(tmp_path, *many)
    assert len(findings) == MAX_FINDINGS and any("beyond the limit" in r for r in reasons)


def test_addition_kind_is_kept_and_unknown_kind_falls_back_to_fix(tmp_path):
    ev = RawEvidence(file_path="main.py", line_start=1, line_end=1)
    findings, _ = _run(
        tmp_path,
        _finding([ev], kind="addition", description="no error handling"),
        _finding([ev], kind="banana", description="something else"),
    )
    assert [f.kind for f in findings] == ["addition", "fix"]
