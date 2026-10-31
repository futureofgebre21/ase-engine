"""The isolated build copy. The untouched clone stays as the baseline for diffs and rollback."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ase.core.errors import PathSafetyError
from ase.security.paths import resolve_within

_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class BuildWorkspace:
    def __init__(self, workspace_root: Path | str) -> None:
        self._dir = Path(workspace_root) / "build"

    def path(self, run_id: str) -> Path:
        if not _RUN_ID_RE.match(run_id):
            raise PathSafetyError("Invalid run id.")
        self._dir.mkdir(parents=True, exist_ok=True)
        return resolve_within(self._dir, run_id)

    def relative_name(self, run_id: str) -> str:
        return f"build/{run_id}"

    def prepare(self, pristine: Path, run_id: str) -> Path:
        """Fresh copy of the clone. Symlinks are copied as links, never followed."""
        dest = self.path(run_id)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(pristine, dest, symlinks=True)
        return dest

    def discard(self, run_id: str) -> None:
        """Roll back: delete the build copy. The baseline clone is untouched."""
        shutil.rmtree(self.path(run_id), ignore_errors=True)
