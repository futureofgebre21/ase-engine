import shutil
from pathlib import Path

import pytest

from ase.core.errors import (
    BudgetExceededError,
    PathSafetyError,
    RepositoryImportError,
    UnsafeUrlError,
)
from ase.repository.importer import ImportLimits, RepositoryImporter

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"
RUN_ID = "a" * 32
URL = "https://github.com/octocat/Hello-World"
LIMITS = ImportLimits(max_files=1000, max_total_bytes=10_000_000, max_file_bytes=1_000_000,
                      timeout_seconds=5)  # fmt: skip


def _copy_fixture(url, dest, timeout):
    shutil.copytree(FIXTURE, dest)


def test_successful_import_uses_canonical_url_and_inventories(tmp_path):
    seen = {}

    def clone(url, dest, timeout):
        seen["url"] = url
        _copy_fixture(url, dest, timeout)

    importer = RepositoryImporter(tmp_path, LIMITS, clone_fn=clone)
    dest, inv = importer.import_repo(URL, RUN_ID)
    assert seen["url"] == "https://github.com/octocat/Hello-World.git"
    assert dest == (tmp_path / "repos" / RUN_ID).resolve() and dest.is_dir()
    assert inv.ecosystems == ["python"]


def test_bad_url_never_reaches_clone(tmp_path):
    called = []
    importer = RepositoryImporter(tmp_path, LIMITS, clone_fn=lambda *a: called.append(a))
    with pytest.raises(UnsafeUrlError):
        importer.import_repo("file:///etc/passwd", RUN_ID)
    assert called == []


def test_invalid_run_id_is_rejected(tmp_path):
    importer = RepositoryImporter(tmp_path, LIMITS, clone_fn=_copy_fixture)
    for bad in ("../x", "short", "Z" * 32):
        with pytest.raises(PathSafetyError):
            importer.import_repo(URL, bad)


def test_escaping_symlink_rejects_import_and_cleans_up(tmp_path, make_symlink):
    outside = tmp_path / "outside"
    outside.mkdir()

    def clone(url, dest, timeout):
        _copy_fixture(url, dest, timeout)
        make_symlink(outside, dest / "evil")

    importer = RepositoryImporter(tmp_path / "ws", LIMITS, clone_fn=clone)
    with pytest.raises(RepositoryImportError):
        importer.import_repo(URL, RUN_ID)
    assert not (tmp_path / "ws" / "repos" / RUN_ID).exists()


def test_clone_failure_cleans_up_partial_workspace(tmp_path):
    def clone(url, dest, timeout):
        dest.mkdir()
        (dest / "partial").write_text("x")
        raise RepositoryImportError("network down")

    importer = RepositoryImporter(tmp_path, LIMITS, clone_fn=clone)
    with pytest.raises(RepositoryImportError):
        importer.import_repo(URL, RUN_ID)
    assert not (tmp_path / "repos" / RUN_ID).exists()


def test_oversized_repo_is_rejected_and_cleaned_up(tmp_path):
    tiny = ImportLimits(max_files=2, max_total_bytes=10_000_000, max_file_bytes=1_000_000,
                        timeout_seconds=5)  # fmt: skip
    importer = RepositoryImporter(tmp_path, tiny, clone_fn=_copy_fixture)
    with pytest.raises(BudgetExceededError):
        importer.import_repo(URL, RUN_ID)
    assert not (tmp_path / "repos" / RUN_ID).exists()
