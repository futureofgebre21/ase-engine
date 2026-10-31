"""Simple JSON-file run storage: one file per run under ``<root>/runs/``.

Chosen over a database because the MVP only needs to save, load and list runs.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ase.core.errors import PathSafetyError
from ase.core.models import RunRecord
from ase.security.paths import resolve_within

_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class JsonRunStore:
    def __init__(self, root: Path | str) -> None:
        self._dir = Path(root) / "runs"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, run_id: str) -> Path:
        if not _RUN_ID_RE.match(run_id):
            raise PathSafetyError("Invalid run id.")
        return resolve_within(self._dir, f"{run_id}.json")

    def save(self, run: RunRecord) -> None:
        path = self._path_for(run.run_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic: readers never see a half-written file

    def load(self, run_id: str) -> RunRecord | None:
        path = self._path_for(run_id)
        if not path.is_file():
            return None
        return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_runs(self, limit: int = 50) -> list[RunRecord]:
        """Newest first. Unreadable or corrupt files are skipped, not fatal."""
        runs: list[RunRecord] = []
        for path in self._dir.glob("*.json"):
            if not _RUN_ID_RE.match(path.stem):
                continue
            try:
                runs.append(RunRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[:limit]
