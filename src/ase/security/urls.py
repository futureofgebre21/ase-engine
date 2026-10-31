"""Repository URL validation (SSRF and credential-leak defence).

Only plain ``https://github.com/<owner>/<repo>`` URLs are accepted for now.
Allowing other hosts is a deliberate future decision, not a default.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ase.core.errors import UnsafeUrlError

ALLOWED_HOSTS: frozenset[str] = frozenset({"github.com"})
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_MAX_LEN = 200


def parse_github_repo_url(url: str) -> tuple[str, str]:
    """Validate ``url`` and return ``(owner, repo)`` or raise ``UnsafeUrlError``."""
    if not isinstance(url, str) or not url or len(url) > _MAX_LEN:
        raise UnsafeUrlError("Repository URL is empty or too long.")
    if "\\" in url or any(c.isspace() or ord(c) < 32 for c in url):
        raise UnsafeUrlError("Repository URL contains forbidden characters.")

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrlError("Repository URL is malformed.") from exc

    if parts.scheme != "https":
        raise UnsafeUrlError("Only https:// repository URLs are allowed.")
    if (parts.hostname or "").lower() not in ALLOWED_HOSTS:
        raise UnsafeUrlError("Only github.com repositories are supported.")
    if parts.username or parts.password:
        raise UnsafeUrlError("Credentials must not be embedded in URLs.")
    if port is not None:
        raise UnsafeUrlError("Custom ports are not allowed.")
    if parts.query or parts.fragment:
        raise UnsafeUrlError("Query strings and fragments are not allowed.")

    segments = [s for s in parts.path.split("/") if s]
    if len(segments) != 2:
        raise UnsafeUrlError("URL must look like https://github.com/<owner>/<repo>.")
    owner, repo = segments
    repo = repo.removesuffix(".git")
    if not _OWNER_RE.match(owner) or not _REPO_RE.match(repo) or repo in {".", ".."}:
        raise UnsafeUrlError("Owner or repository name is invalid.")
    return owner, repo


def canonical_clone_url(url: str) -> str:
    owner, repo = parse_github_repo_url(url)
    return f"https://github.com/{owner}/{repo}.git"
