import pytest

from ase.core.errors import PathSafetyError
from ase.repository.build_workspace import BuildWorkspace

RUN = "b" * 32


def _clone(tmp_path):
    clone = tmp_path / "repos" / RUN
    clone.mkdir(parents=True)
    (clone / "a.py").write_text("x = 1\n")
    (clone / "sub").mkdir()
    (clone / "sub" / "b.py").write_text("y = 2\n")
    return clone


def test_prepare_copies_and_leaves_the_baseline_untouched(tmp_path):
    clone = _clone(tmp_path)
    ws = BuildWorkspace(tmp_path)
    build = ws.prepare(clone, RUN)
    assert build == (tmp_path / "build" / RUN).resolve()
    assert (build / "sub" / "b.py").read_text() == "y = 2\n"
    (build / "a.py").write_text("x = 999\n")  # the build copy changes...
    assert (clone / "a.py").read_text() == "x = 1\n"  # ...the baseline does not


def test_prepare_again_resets_the_copy(tmp_path):
    clone = _clone(tmp_path)
    ws = BuildWorkspace(tmp_path)
    build = ws.prepare(clone, RUN)
    (build / "stale.txt").write_text("old build")
    ws.prepare(clone, RUN)
    assert not (build / "stale.txt").exists()


def test_discard_rolls_back_without_touching_the_baseline(tmp_path):
    clone = _clone(tmp_path)
    ws = BuildWorkspace(tmp_path)
    build = ws.prepare(clone, RUN)
    ws.discard(RUN)
    assert not build.exists() and (clone / "a.py").exists()


def test_invalid_run_ids_are_rejected(tmp_path):
    ws = BuildWorkspace(tmp_path)
    for bad in ("../x", "short", "Z" * 32, ""):
        with pytest.raises(PathSafetyError):
            ws.path(bad)


def test_symlinks_are_copied_as_links_not_followed(tmp_path, make_symlink):
    clone = _clone(tmp_path)
    secret = tmp_path / "outside.txt"
    secret.write_text("private")
    make_symlink(secret, clone / "link.txt")
    build = BuildWorkspace(tmp_path).prepare(clone, RUN)
    assert (build / "link.txt").is_symlink()  # still a link, so nothing was copied in
