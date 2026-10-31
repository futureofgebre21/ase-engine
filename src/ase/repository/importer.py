"""Import a public GitHub repository into an isolated workspace directory."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ase.core.config import Settings
from ase.core.errors import AseError, PathSafetyError, RepositoryImportError
from ase.core.models import RepositoryInventory
from ase.repository.gitclone import clone_with_git
from ase.repository.inventory import build_inventory
from ase.security.paths import find_escaping_symlinks, resolve_within
from ase.security.urls import canonical_clone_url

CloneFn = Callable[[str, Path, int], None]
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class ImportLimits:
    max_files: int
    max_total_bytes: int
    max_file_bytes: int
    timeout_seconds: int

    @classmethod
    def from_settings(cls, s: Settings) -> ImportLimits:
        return cls(s.max_files, s.max_repo_bytes, s.max_file_bytes, s.import_timeout_seconds)


class RepositoryImporter:
    def __init__(
        self, workspace_root: Path | str, limits: ImportLimits, clone_fn: CloneFn = clone_with_git
    ) -> None:
        self._repos_dir = Path(workspace_root) / "repos"
        self._limits = limits
        self._clone = clone_fn

    def workspace_path(self, run_id: str) -> Path:
        if not _RUN_ID_RE.match(run_id):
            raise PathSafetyError("Invalid run id.")
        self._repos_dir.mkdir(parents=True, exist_ok=True)
        return resolve_within(self._repos_dir, run_id)

    def import_repo(self, url: str, run_id: str) -> tuple[Path, RepositoryInventory]:
        """Clone, verify, inventory. On ANY failure the partial workspace is deleted.

        Raises ``UnsafeUrlError`` (bad URL) or another ``AseError`` (import failed).
        """
        clone_url = canonical_clone_url(url)  # validates; raises UnsafeUrlError
        dest = self.workspace_path(run_id)
        try:
            self._clone(clone_url, dest, self._limits.timeout_seconds)
            if not dest.is_dir():
                raise RepositoryImportError("Clone did not produce a directory.")
            escaping = find_escaping_symlinks(dest)
            if escaping:
                raise RepositoryImportError(
                    f"Repository contains {len(escaping)} symlink(s) pointing outside itself."
                )
            inventory = build_inventory(
                dest,
                max_files=self._limits.max_files,
                max_total_bytes=self._limits.max_total_bytes,
                max_file_bytes=self._limits.max_file_bytes,
            )
        except (AseError, OSError):
            shutil.rmtree(dest, ignore_errors=True)
            raise
        return dest, inventory
