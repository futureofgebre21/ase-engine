import shutil
from pathlib import Path

from ase.agent.plan_validation import (
    UNADDRESSED_REASON,
    is_forbidden_target,
    normalize_plan_path,
    validate_raw_plan,
)
from ase.core.models import Evidence, Finding, Severity
from ase.llm.schemas import RawChange, RawDeferred, RawPlan
from ase.repository.inventory import build_inventory

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"
SENT = {"cart.py", "main.py", "tests/test_cart.py", "README.md"}  # files "shown to the model"


def _finding(fid):
    return Finding(
        id=fid, severity=Severity.HIGH, category="bug", description=f"problem {fid}",
        source="llm", confidence=0.9,
        evidence=[Evidence(file_path="cart.py", line_start=6, line_end=6, location_verified=True)],
    )  # fmt: skip


def _change(path="cart.py", action="modify", ids=("F-001",), **kw):
    base = dict(action=action, path=path, summary="fix the discount", finding_ids=list(ids),
                verification="run tests", risk="low")  # fmt: skip
    base.update(kw)
    return RawChange(**base)


def _validate(tmp_path, changes, deferred=(), findings=("F-001",), max_changes=15, **plan_kw):
    root = tmp_path / "repo"
    if not root.exists():
        shutil.copytree(FIXTURE, root)
    inv = build_inventory(root, max_files=1000, max_total_bytes=10**7, max_file_bytes=10**6)
    raw = RawPlan(changes=list(changes), deferred=list(deferred), **plan_kw)
    return validate_raw_plan(
        raw, inventory=inv, findings=[_finding(i) for i in findings],
        sent_paths=SENT, max_changes=max_changes,
    )  # fmt: skip


def test_valid_modify_and_create_are_kept_with_ase_assigned_ids(tmp_path):
    out = _validate(
        tmp_path,
        [_change(), _change("tests/test_discount.py", "create", summary="cover discounts")],
        architecture_summary="small module",
    )
    assert out.rejection_reasons == []
    assert [c.id for c in out.changes] == ["C-001", "C-002"]
    assert [(c.action, c.path) for c in out.changes] == [
        ("modify", "cart.py"), ("create", "tests/test_discount.py")]  # fmt: skip
    assert out.deferred == []  # F-001 is covered


def test_unsafe_paths_are_rejected(tmp_path):
    bad = ["/etc/passwd", "../x.py", "a/../../x.py", "C:/x.py", "a\\b.py", "", "   ",
           "x" * 300, "a\x00b.py"]  # fmt: skip
    for path in bad:
        out = _validate(tmp_path, [_change(path, "create")])
        assert out.changes == [], path
        assert out.rejection_reasons == ["unsafe or malformed path"], path


def test_forbidden_targets_are_rejected(tmp_path):
    for path in (".git/config", ".env.production", "logo.png", "package-lock.json",
                 "node_modules/x/index.js", "build/out.js", "key.pem", "app.min.js"):  # fmt: skip
        out = _validate(tmp_path, [_change(path, "create")])
        assert out.changes == [], path
        assert is_forbidden_target(path)
    assert not is_forbidden_target("src/app/main.py")


def test_modify_must_target_a_file_the_model_was_shown(tmp_path):
    for path in ("ghost.py", "requirements.txt"):  # missing / exists but was not shown
        out = _validate(tmp_path, [_change(path, "modify")])
        assert out.changes == [] and "does not exist or was not shown" in out.rejection_reasons[0]


def test_create_cannot_overwrite_or_collide(tmp_path):
    for path in ("cart.py", "CART.PY", "cart.py/inner.py", "tests/test_cart.py"):
        out = _validate(tmp_path, [_change(path, "create")])
        assert out.changes == [], path
        assert out.rejection_reasons == ["create target already exists (use modify)"], path


def test_delete_and_other_actions_are_not_allowed(tmp_path):
    for action in ("delete", "rename", "", "MODIFY!"):
        out = _validate(tmp_path, [_change(action=action)])
        assert out.changes == [] and out.rejection_reasons == ["invalid action"], action


def test_every_change_must_be_tied_to_a_known_finding(tmp_path):
    out = _validate(tmp_path, [_change(ids=()), _change(ids=("F-999",), path="main.py")])
    assert out.changes == []
    assert out.rejection_reasons == ["change is not tied to a known finding"] * 2
    out = _validate(tmp_path, [_change(ids=("F-001", "F-999", "F-001"))])
    assert out.changes[0].finding_ids == ["F-001"]  # unknown and repeated ids dropped


def test_duplicate_paths_and_the_change_limit(tmp_path):
    out = _validate(tmp_path, [_change(), _change(summary="again")])
    assert len(out.changes) == 1 and out.rejection_reasons == ["duplicate path"]
    many = [_change(f"new/f{i}.py", "create") for i in range(5)]
    out = _validate(tmp_path, many, max_changes=3)
    assert len(out.changes) == 3
    assert out.rejection_reasons == ["beyond the limit of 3 changes"] * 2


def test_invalid_risk_defaults_to_medium_and_secrets_are_redacted(tmp_path):
    secret = "ghp_" + "a" * 36
    out = _validate(tmp_path, [_change(risk="apocalyptic", summary=f"rotate {secret}")],
                    test_strategy=f"use {secret}", risks=[f"leak {secret}"])  # fmt: skip
    assert out.changes[0].risk == "medium"
    assert secret not in out.changes[0].summary and secret not in out.test_strategy
    assert secret not in out.risks[0]


def test_every_finding_is_accounted_for(tmp_path):
    out = _validate(
        tmp_path,
        [_change(ids=("F-001",))],
        deferred=[
            RawDeferred(finding_id="F-002", reason="needs a product decision"),
            RawDeferred(finding_id="F-001", reason="already covered, ignored"),
            RawDeferred(finding_id="F-777", reason="unknown id, ignored"),
        ],
        findings=("F-001", "F-002", "F-003"),
    )
    by_id = {d.finding_id: d.reason for d in out.deferred}
    assert by_id == {"F-002": "needs a product decision", "F-003": UNADDRESSED_REASON}


def test_normalize_plan_path_cleans_harmless_variants():
    assert normalize_plan_path("./src//app/main.py") == "src/app/main.py"
    assert normalize_plan_path(None) is None and normalize_plan_path(5) is None
