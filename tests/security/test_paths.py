import pytest

from ase.core.errors import PathSafetyError
from ase.security.paths import find_escaping_symlinks, is_within, resolve_within


def test_relative_path_inside_root_is_allowed(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x")
    assert resolve_within(tmp_path, "src/a.py") == (tmp_path / "src" / "a.py").resolve()


def test_nonexistent_file_inside_root_is_allowed(tmp_path):
    assert resolve_within(tmp_path, "new/file.py").is_relative_to(tmp_path.resolve())


def test_dotdot_traversal_is_rejected(tmp_path):
    for bad in ("../x", "a/../../x", "a/b/../../../etc/passwd"):
        with pytest.raises(PathSafetyError):
            resolve_within(tmp_path, bad)


def test_absolute_path_outside_root_is_rejected(tmp_path):
    with pytest.raises(PathSafetyError):
        resolve_within(tmp_path, "/etc/passwd")


def test_null_byte_is_rejected(tmp_path):
    with pytest.raises(PathSafetyError):
        resolve_within(tmp_path, "a\x00b")


def test_missing_root_is_rejected(tmp_path):
    with pytest.raises(PathSafetyError):
        resolve_within(tmp_path / "nope", "a")


def test_sibling_directory_with_shared_prefix_is_rejected(tmp_path):
    root = tmp_path / "ws"
    sibling = tmp_path / "ws-evil"
    root.mkdir()
    sibling.mkdir()
    assert not is_within(root, sibling / "f")


def test_symlink_escape_is_rejected(tmp_path, make_symlink):
    root = tmp_path / "ws"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    make_symlink(outside, root / "link")
    with pytest.raises(PathSafetyError):
        resolve_within(root, "link/secret.txt")


def test_find_escaping_symlinks_reports_only_bad_links(tmp_path, make_symlink):
    root = tmp_path / "ws"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "real.txt").write_text("ok")
    make_symlink(root / "real.txt", root / "good_link")
    make_symlink(outside, root / "bad_link")
    found = find_escaping_symlinks(root)
    assert [p.name for p in found] == ["bad_link"]
