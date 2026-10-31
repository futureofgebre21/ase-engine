"""The only sanctioned way to read repository files for a model prompt (used from Phase 5)."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from ase.core.errors import AseError, PolicyViolationError
from ase.core.models import InventoryEntry, RepositoryInventory
from ase.security.paths import resolve_within
from ase.security.redaction import is_sensitive_path, redact

DEFAULT_MAX_BYTES = 20_000


def read_file_for_model(
    workspace: Path | str, rel_path: str, max_bytes: int = DEFAULT_MAX_BYTES
) -> str:
    """Return redacted, size-bounded text, or raise ``PolicyViolationError`` / ``PathSafetyError``.

    The returned text is DATA from an untrusted repository, never instructions.
    """
    if is_sensitive_path(rel_path):
        raise PolicyViolationError("File is excluded from model context (sensitive).")
    root = Path(workspace)
    if (root / rel_path).is_symlink():
        raise PolicyViolationError("Symlinks are excluded from model context.")
    path = resolve_within(root, rel_path)
    if is_sensitive_path(path.name):
        raise PolicyViolationError("File is excluded from model context (sensitive).")
    if not path.is_file():
        raise PolicyViolationError("Not a regular file.")
    with path.open("rb") as fh:
        data = fh.read(max_bytes + 1)
    if b"\x00" in data[:4096]:
        raise PolicyViolationError("Binary files are excluded from model context.")
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # same text on Windows and Linux
    if truncated:
        text += "\n[truncated]"
    return redact(text)


_DOC_SUFFIXES = frozenset({".md", ".rst", ".txt"})
_CONFIG_SUFFIXES = frozenset({".json", ".yml", ".yaml", ".toml", ".xml", ".ini", ".cfg",
                              ".properties", ".gradle"})  # fmt: skip
_NOISE_NAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock", "poetry.lock",
    "pipfile.lock", "composer.lock", "gemfile.lock", "go.sum", "uv.lock",
})  # fmt: skip
_NOISE_SUFFIXES = (".min.js", ".min.css", ".map", ".lock")


def is_noise_file(file_name: str) -> bool:
    """Lockfiles, minified bundles and source maps: large, generated, never edited by hand."""
    name = file_name.lower()
    return name in _NOISE_NAMES or name.endswith(_NOISE_SUFFIXES)
MAX_SPAN_LINES = 200


def select_context_files(
    workspace: Path | str,
    inventory: RepositoryInventory,
    *,
    max_files: int,
    max_total_bytes: int,
    max_file_bytes: int = DEFAULT_MAX_BYTES,
) -> list[tuple[str, str]]:
    """Pick the most useful non-excluded files and return ``(path, redacted_text)`` pairs.

    Order: entry points, manifests and build/CI files, source, tests, docs, config, markup.
    Never includes files the inventory excluded (secrets, binaries, oversized, symlinks).
    """
    entry = set(inventory.entry_points)
    deps = set(inventory.dependency_files)
    tests = set(inventory.test_files)
    devops = set(inventory.devops_files)

    def priority(e: InventoryEntry) -> int | None:
        if is_noise_file(PurePosixPath(e.path).name):
            return None  # large, generated and not informative
        if e.path in entry:
            return 0
        if e.path in deps or e.path in devops:
            return 1
        if e.path in tests:
            return 3 if e.language else None
        if e.language and e.language not in {"HTML", "CSS"}:
            return 2
        suffix = PurePosixPath(e.path).suffix.lower()
        if suffix in _DOC_SUFFIXES:
            return 4
        if suffix in _CONFIG_SUFFIXES:
            return 5
        if e.language:  # HTML / CSS
            return 6
        return None

    ranked = sorted(
        ((p, e) for e in inventory.files if e.excluded is None and (p := priority(e)) is not None),
        key=lambda pe: (pe[0], pe[1].size_bytes, pe[1].path),
    )
    chosen: list[tuple[str, str]] = []
    total = 0
    for _, e in ranked:
        if len(chosen) >= max_files or total >= max_total_bytes:
            break
        try:
            text = read_file_for_model(workspace, e.path, max_file_bytes)
        except AseError:
            continue
        if total + len(text) > max_total_bytes:
            continue
        chosen.append((e.path, text))
        total += len(text)
    return chosen
