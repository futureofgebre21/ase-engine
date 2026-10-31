from ase.core.models import RunRecord
from ase.core.states import RunState
from ase.ui.presenters import NO_PROVIDER_BANNER, STATE_LABELS, history_rows, run_row


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
