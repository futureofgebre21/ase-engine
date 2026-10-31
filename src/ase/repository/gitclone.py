"""Hardened ``git clone`` wrapper. Standard library only.

Hardening applied:
- no shell; argument list only; ``--`` before the URL
- only https transport allowed; no submodule recursion
- ``core.symlinks=false`` so symlinks in the repo become plain text files
- shallow, single-branch clone, no tags
- prompts disabled; global/system git config ignored
- a minimal environment, so API keys and tokens in the parent environment are NOT passed to git
- hard timeout, bounded and redacted error output
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ase.core.errors import RepositoryImportError
from ase.security.redaction import redact

_ENV_ALLOWLIST = (
    "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
)  # fmt: skip


def build_clone_command(
    git: str, clone_url: str, dest: Path, check_revocation: bool = True
) -> list[str]:
    extra = [] if check_revocation else ["-c", "http.schannelCheckRevoke=false"]
    return [
        git,
        *extra,
        "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always",
        "-c", "core.symlinks=false",
        "clone",
        "--depth", "1",
        "--single-branch",
        "--no-tags",
        "--",
        clone_url,
        str(dest),
    ]  # fmt: skip


def clean_git_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def clone_with_git(
    clone_url: str, dest: Path, timeout_seconds: int, check_revocation: bool = True
) -> None:
    """Clone ``clone_url`` into ``dest`` (which must not exist) or raise."""
    git = shutil.which("git")
    if git is None:
        raise RepositoryImportError("Git is not installed or not on PATH.")
    if dest.exists():
        raise RepositoryImportError("Destination already exists.")
    cmd = build_clone_command(git, clone_url, dest, check_revocation)
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, validated URL
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=clean_git_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepositoryImportError(f"Clone timed out after {timeout_seconds}s.") from exc
    except OSError as exc:
        raise RepositoryImportError("Git could not be started.") from exc
    if result.returncode != 0:
        detail = redact((result.stderr or "").strip()[-400:])
        raise RepositoryImportError(f"git clone failed (exit {result.returncode}): {detail}")
