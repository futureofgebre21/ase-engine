import pytest

from ase.core.errors import PathSafetyError
from ase.core.models import RunRecord
from ase.core.states import RunState
from ase.storage.run_store import JsonRunStore


def test_save_and_load_roundtrip(tmp_path):
    store = JsonRunStore(tmp_path)
    run = RunRecord(source="github.com/a/b")
    run.transition(RunState.IMPORTED)
    store.save(run)
    loaded = store.load(run.run_id)
    assert loaded == run


def test_load_missing_returns_none(tmp_path):
    assert JsonRunStore(tmp_path).load("0" * 32) is None


def test_invalid_run_ids_are_rejected(tmp_path):
    store = JsonRunStore(tmp_path)
    for bad in ("../../etc/passwd", "abc", "G" * 32, "", "a" * 31 + "/"):
        with pytest.raises(PathSafetyError):
            store.load(bad)


def test_list_is_newest_first_and_skips_corrupt_files(tmp_path):
    store = JsonRunStore(tmp_path)
    first, second = RunRecord(), RunRecord()
    store.save(first)
    store.save(second)
    (tmp_path / "runs" / ("f" * 32 + ".json")).write_text("{not json")
    runs = store.list_runs()
    assert [r.run_id for r in runs] == [second.run_id, first.run_id]


def test_list_respects_limit(tmp_path):
    store = JsonRunStore(tmp_path)
    for _ in range(5):
        store.save(RunRecord())
    assert len(store.list_runs(limit=3)) == 3
