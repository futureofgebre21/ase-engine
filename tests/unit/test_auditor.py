import json
import shutil
from pathlib import Path

import pytest

from ase.agent.auditor import AuditLimits, Auditor
from ase.core.errors import BudgetExceededError, LLMResponseError, LLMTransientError
from ase.repository.inventory import build_inventory

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"
GOOD = json.dumps({
    "summary": "Discount logic is inverted.",
    "findings": [{
        "severity": "high", "category": "logic-error", "description": "Discount is added.",
        "evidence": [{"file_path": "cart.py", "line_start": 6, "line_end": 6,
                      "quote": "return price + price * percent / 100"}],
        "recommended_fix": "Subtract the discount.", "confidence": 0.9,
    }],
})  # fmt: skip


def _setup(provider_cls, tmp_path, responses, **limit_overrides):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    inv = build_inventory(root, max_files=1000, max_total_bytes=10**7, max_file_bytes=10**6)
    provider = provider_cls(responses)
    limits = dict(max_model_calls=5, max_context_files=12, max_context_bytes=60_000,
                  base_delay_seconds=0.0)  # fmt: skip
    limits.update(limit_overrides)
    return root, inv, provider, Auditor(provider, AuditLimits(**limits), sleep=lambda s: None)


def test_happy_path_reports_real_counts_and_unknown_cost(tmp_path, scripted_provider):
    root, inv, provider, auditor = _setup(scripted_provider, tmp_path, [GOOD])
    report = auditor.audit(root, inv)
    assert report.provider == "scripted-test-double" and report.model_calls == 1
    assert [f.id for f in report.findings] == ["F-001"] and report.rejected_findings == 0
    assert report.cost_usd is None and report.prompt_tokens is None  # unknown, not invented


def test_secret_and_binary_files_are_never_sent(tmp_path, scripted_provider):
    root, inv, provider, auditor = _setup(scripted_provider, tmp_path, [GOOD])
    report = auditor.audit(root, inv)
    assert ".env.production" not in report.files_sent and "logo.png" not in report.files_sent
    sent = " ".join(f.content for f in provider.requests[0].context_files)
    assert "not-a-real-secret" not in sent
    assert "cart.py" in report.files_sent and "main.py" in report.files_sent


def test_injected_readme_is_sent_only_as_untrusted_context(tmp_path, scripted_provider):
    root, inv, provider, auditor = _setup(scripted_provider, tmp_path, [GOOD])
    auditor.audit(root, inv)
    req = provider.requests[0]
    readme = next(f for f in req.context_files if f.path == "README.md")
    assert "Ignore all previous instructions" in readme.content  # present as data...
    assert "Ignore all previous instructions" not in req.system  # ...never in instructions
    assert "Ignore all previous instructions" not in req.task


def test_malformed_then_valid_response_costs_two_calls(tmp_path, scripted_provider):
    root, inv, provider, auditor = _setup(scripted_provider, tmp_path, ["I cannot do JSON", GOOD])
    report = auditor.audit(root, inv)
    assert report.model_calls == 2 and len(report.findings) == 1


def test_persistently_malformed_response_fails_safely(tmp_path, scripted_provider):
    root, inv, provider, auditor = _setup(scripted_provider, tmp_path, ["nope", "still nope", GOOD])
    with pytest.raises(LLMResponseError):
        auditor.audit(root, inv)
    assert len(provider.requests) == 2  # bounded: the third scripted reply is never used


def test_transient_errors_are_retried_and_count_against_budget(tmp_path, scripted_provider):
    errs = [LLMTransientError("429"), LLMTransientError("503"), GOOD]
    root, inv, provider, auditor = _setup(scripted_provider, tmp_path, errs)
    assert auditor.audit(root, inv).model_calls == 3


def test_model_call_budget_is_enforced(tmp_path, scripted_provider):
    errs = [LLMTransientError("x")] * 3
    root, inv, provider, auditor = _setup(scripted_provider, tmp_path, errs, max_model_calls=2)
    with pytest.raises(BudgetExceededError):
        auditor.audit(root, inv)
    assert len(provider.requests) == 2


def test_hallucinated_findings_are_counted_not_shown(tmp_path, scripted_provider):
    fake = json.dumps({"summary": "s", "findings": [{
        "severity": "critical", "category": "rce", "description": "Remote code execution",
        "evidence": [{"file_path": "server.py", "line_start": 10, "line_end": 12}],
        "recommended_fix": "x", "confidence": 1.0}]})  # fmt: skip
    root, inv, _, auditor = _setup(scripted_provider, tmp_path, [fake])
    report = auditor.audit(root, inv)
    assert report.findings == [] and report.rejected_findings == 1
