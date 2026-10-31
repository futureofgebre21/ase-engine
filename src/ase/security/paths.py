"""Workspace path safety.

All file access derived from untrusted input (repo contents, model output,
user text) must go through ``resolve_within``.

Known limitation: checking a path and then using it is not atomic (TOCTOU).
Strong protection comes from the isolated execution environment (later phase);
this module is the first, deterministic layer.
"""

from __future__ import annotations

import os
from pathlib import Path

from ase.core.errors import PathSafetyError


def resolve_within(root: Path | str, candidate: Path | str) -> Path:
    """Return the fully resolved ``candidate`` or raise ``PathSafetyError``.

    Relative candidates are interpreted relative to ``root``. Symlinks are
    followed *before* the containment check, so a link pointing outside the
    root is rejected.
    """
    if "\x00" in str(candidate):
        raise PathSafetyError("Path contains a null byte.")
    try:
        root_resolved = Path(root).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise PathSafetyError("Workspace root does not exist.") from exc

    cand = Path(candidate)
    joined = cand if cand.is_absolute() else root_resolved / cand
    try:
        resolved = joined.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:  # e.g. symlink loops
        raise PathSafetyError("Path could not be resolved safely.") from exc

    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PathSafetyError("Path is outside the workspace.") from exc
    return resolved


def is_within(root: Path | str, candidate: Path | str) -> bool:
    try:
        resolve_within(root, candidate)
    except PathSafetyError:
        return False
    return True


def find_escaping_symlinks(root: Path | str) -> list[Path]:
    """List symlinks under ``root`` whose targets resolve outside ``root``.

    Used after importing a repository, before anything reads its files.
    """
    root_resolved = Path(root).resolve(strict=True)
    bad: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root_resolved, followlinks=False):
        for name in [*dirnames, *filenames]:
            p = Path(dirpath) / name
            if p.is_symlink() and not is_within(root_resolved, p):
                bad.append(p)
    return sorted(bad)
