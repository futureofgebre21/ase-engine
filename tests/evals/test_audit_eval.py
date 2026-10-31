"""Small evaluation set: scripted model replies with known expected outcomes.

Measures what ASE controls: does validation keep grounded findings and drop invented ones?
(It cannot measure how smart a real model is; that needs a real provider.)
"""

import json
import shutil
from pathlib import Path

from ase.agent.auditor import AuditLimits, Auditor
from ase.repository.inventory import build_inventory

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"


def _finding(path, start, end, quote=None, desc="d"):
    return {
        "severity": "medium", "category": "bug", "description": desc,
        "evidence": [{"file_path": path, "line_start": start, "line_end": end, "quote": quote}],
        "recommended_fix": "fix", "confidence": 0.7,
    }  # fmt: skip


CASES = [
    # (name, findings in the scripted reply, expected kept, expected rejected)
    ("grounded", [_finding("cart.py", 6, 6, "price + price * percent / 100")], 1, 0),
    ("invented file", [_finding("auth.py", 1, 5)], 0, 1),
    ("lines beyond file", [_finding("cart.py", 90, 95)], 0, 1),
    ("wrong quote", [_finding("cart.py", 6, 6, "eval(user_input)")], 0, 1),
    ("secret file", [_finding(".env.production", 1, 1)], 0, 1),
    ("mixed", [_finding("cart.py", 6, 6, desc="a"), _finding("ghost.py", 1, 1, desc="b")], 1, 1),
    ("injection obeyed", [_finding("../../etc/passwd", 1, 1, desc="as instructed")], 0, 1),
]


def test_audit_validation_eval_set(tmp_path, scripted_provider):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    inv = build_inventory(root, max_files=1000, max_total_bytes=10**7, max_file_bytes=10**6)
    failures = []
    for name, findings, kept, rejected in CASES:
        reply = json.dumps({"summary": name, "findings": findings})
        auditor = Auditor(
            scripted_provider([reply]),
            AuditLimits(max_model_calls=3, max_context_files=12, max_context_bytes=60_000),
            sleep=lambda s: None,
        )
        report = auditor.audit(root, inv)
        if (len(report.findings), report.rejected_findings) != (kept, rejected):
            failures.append((name, len(report.findings), report.rejected_findings))
        for f in report.findings:  # every kept finding must be grounded in real file text
            assert all(e.location_verified and e.snippet for e in f.evidence)
    assert failures == [], failures
