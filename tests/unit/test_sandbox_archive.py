import tarfile

import pytest

from ase.core.errors import SandboxError
from ase.execution.sandbox import build_archive


def _names(archive):
    with tarfile.open(archive) as tar:
        return sorted(m.name for m in tar.getmembers())


def test_the_archive_holds_project_files_and_nothing_sensitive(tmp_path):
    for rel, text in {
        "app.py": "x = 1", "pkg/mod.py": "y = 2", ".env": "SECRET=1", ".env.production": "K=1",
        "server.pem": "key", ".git/config": "x", "node_modules/x/i.js": "x",
        "__pycache__/a.pyc": "x", ".venv/lib/x.py": "x", "keep.env.example": "A=",
    }.items():  # fmt: skip
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    archive = build_archive(tmp_path)
    try:
        assert _names(archive) == ["app.py", "keep.env.example", "pkg/mod.py"]
    finally:
        archive.unlink()


def test_symlinks_are_never_archived(tmp_path, make_symlink):
    (tmp_path / "real.py").write_text("x = 1")
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("private")
    make_symlink(outside, tmp_path / "link.txt")
    archive = build_archive(tmp_path)
    try:
        assert _names(archive) == ["real.py"]
    finally:
        archive.unlink()


def test_archive_owner_information_is_stripped(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    archive = build_archive(tmp_path)
    try:
        with tarfile.open(archive) as tar:
            member = tar.getmembers()[0]
            assert (member.uid, member.gid, member.uname, member.gname) == (0, 0, "", "")
    finally:
        archive.unlink()


def test_oversized_projects_are_refused_and_leave_no_temp_file(tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "big.txt").write_text("a" * 1000)
    with pytest.raises(SandboxError):
        build_archive(project, max_bytes=100)
    assert list((tmp_path / "tmp").glob("ase-*.tgz")) == []  # nothing left behind
