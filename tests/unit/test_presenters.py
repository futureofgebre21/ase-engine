from ase.core.models import AuditReport, BuildReport, Plan, RunRecord
from ase.core.states import RunState
from ase.ui.presenters import (
    NO_PROVIDER_BANNER,
    STATE_LABELS,
    history_rows,
    run_row,
    usage_rows,
)


def test_every_state_has_a_label():
    assert set(STATE_LABELS) == set(RunState)


def test_run_row_handles_missing_source():
    row = run_row(RunRecord())
    assert row["Source"] == "-" and row["State"] == "Created" and len(row["Run"]) == 8


def test_history_rows_follow_transitions():
    run = RunRecord()
    run.transition(RunState.IMPORTED, reason="ok")
    assert history_rows(run)[0]["To"] == "Imported"


def test_banner_tells_the_truth_about_a_missing_provider():
    assert "No AI provider is configured" in NO_PROVIDER_BANNER
    assert "ASE_LLM" in NO_PROVIDER_BANNER


def _meta(prompt, completion, calls, provider):
    return dict(provider=provider, model="muse-spark-open", prompt_version="v", model_calls=calls,
                prompt_tokens=prompt, completion_tokens=completion)  # fmt: skip


def test_usage_table_is_empty_before_any_ai_step():
    assert usage_rows(RunRecord()) == []


def test_usage_table_reports_real_values_and_totals():
    run = RunRecord()
    run.audit = AuditReport(**_meta(1000, 200, 1, "baseten"))
    run.plan = Plan(**_meta(2000, 300, 1, "baseten"))
    run.build = BuildReport(**_meta(5000, 900, 4, "modal"))
    rows = usage_rows(run)
    assert [r["Step"] for r in rows] == ["2. Audit", "3. Plan", "4. Build", "Total"]
    assert [r["Provider"] for r in rows[:3]] == ["baseten", "baseten", "modal"]
    assert rows[3]["Model calls"] == 6
    assert rows[3]["Tokens in"] == "8,000" and rows[3]["Tokens out"] == "1,400"


def test_unknown_token_counts_are_never_summed_into_a_fake_total():
    run = RunRecord()
    run.audit = AuditReport(**_meta(1000, 200, 1, "baseten"))
    run.plan = Plan(**_meta(None, None, 1, "baseten"))
    rows = usage_rows(run)
    assert rows[1]["Tokens in"] == "unknown"
    assert rows[2]["Tokens in"] == "unknown" and rows[2]["Tokens out"] == "unknown"
