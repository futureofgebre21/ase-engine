"""Secret redaction and sensitive-file detection.

Pattern matching is best-effort, not a guarantee. Redact before logging,
before sending text to a model, and before showing command output in the UI.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

REDACTED = "[REDACTED]"

_TOKEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
]

def contains_secret_token(text: str) -> bool:
    """True if ``text`` holds a known credential format (private key, GitHub/AWS/API token).

    Unlike ``redact`` this ignores variable names such as ``password = ...``, which are normal
    in code, so it is safe to use on generated code.
    """
    return any(p.search(text) for p in _TOKEN_PATTERNS)


_ASSIGNMENT = re.compile(
    r"(?im)^(\s*(?:export\s+)?[A-Za-z0-9_.-]*"
    r"(?:secret|token|password|passwd|api[_-]?key|private[_-]?key)"
    r"[A-Za-z0-9_.-]*\s*[=:]\s*)(\S.*)$"
)


def redact(text: str) -> str:
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return _ASSIGNMENT.sub(lambda m: m.group(1) + REDACTED, text)


_SENSITIVE_NAMES = frozenset(
    {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".npmrc", ".pypirc", ".netrc"}
)
_SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx"})
_ENV_SAFE_SUFFIXES = (".example", ".sample", ".template")


def is_sensitive_path(path: str) -> bool:
    """True if a file should be excluded from model context by default."""
    p = PurePosixPath(path.replace("\\", "/"))
    name = p.name.lower()
    if name == ".env" or (name.startswith(".env.") and not name.endswith(_ENV_SAFE_SUFFIXES)):
        return True
    return name in _SENSITIVE_NAMES or p.suffix.lower() in _SENSITIVE_SUFFIXES
