"""Apply and check generated changes. Pure functions: no model, no network, no execution.

Existing files change only through exact search-and-replace on their REAL text, so secret-like
values that were hidden from the model are never touched or overwritten.
"""

from __future__ import annotations

import ast
import difflib
import json
import tomllib
import warnings
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Literal

from ase.core.errors import ChangeRejected
from ase.llm.schemas import RawEdit
from ase.security.redaction import REDACTED, contains_secret_token

MAX_EDITS = 30
DIFF_CAP_CHARS = 40_000
SyntaxStatus = Literal["passed", "failed", "not_checked"]


def decode_text(raw: bytes) -> tuple[str, str, bool]:
    """Return ``(text with \\n endings, original newline style, had_bom)``."""
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = (raw[3:] if bom else raw).decode("utf-8")
    except UnicodeDecodeError:
        raise ChangeRejected("the file is not valid UTF-8 text") from None
    if "\x00" in text:
        raise ChangeRejected("the file looks binary")
    crlf = text.count("\r\n")
    newline = "\r\n" if crlf > text.count("\n") - crlf else "\n"
    return text.replace("\r\n", "\n"), newline, bom


def encode_text(text: str, newline: str = "\n", bom: bool = False) -> bytes:
    out = text.replace("\n", newline) if newline != "\n" else text
    data = out.encode("utf-8")
    return b"\xef\xbb\xbf" + data if bom else data


def check_generated_text(text: str, *, max_bytes: int) -> None:
    """Checks that apply to any text the model produced, before it can be written."""
    if "\x00" in text:
        raise ChangeRejected("the generated text contains a null byte")
    if REDACTED in text:
        raise ChangeRejected("the generated text contains the [REDACTED] marker")
    if contains_secret_token(text):
        raise ChangeRejected("the generated text contains a secret-like value")
    if len(text.encode("utf-8")) > max_bytes:
        raise ChangeRejected(f"the generated file is larger than {max_bytes} bytes")


def apply_edits(original: str, edits: Sequence[RawEdit], *, max_bytes: int) -> str:
    """Apply search-and-replace edits in order; each ``old`` must match exactly once."""
    if not edits:
        raise ChangeRejected("the reply contained no edits")
    if len(edits) > MAX_EDITS:
        raise ChangeRejected(f"too many edits (limit {MAX_EDITS})")
    text = original
    for n, edit in enumerate(edits, start=1):
        old = edit.old.replace("\r\n", "\n")
        new = edit.new.replace("\r\n", "\n")
        if not old.strip():
            raise ChangeRejected(f"edit {n}: the text to replace is empty")
        if REDACTED in old:
            raise ChangeRejected(
                f"edit {n}: it touches text that was hidden from the model (secret-like values)"
            )
        if old == new:
            raise ChangeRejected(f"edit {n}: old and new text are identical")
        found = text.count(old)
        if found != 1:
            raise ChangeRejected(
                f"edit {n}: the text to replace was found {found} times; it must appear exactly "
                "once, copied exactly from the file (include more surrounding lines)"
            )
        check_generated_text(new, max_bytes=max_bytes)
        text = text.replace(old, new, 1)
    if text == original:
        raise ChangeRejected("the edits produced no change")
    check_generated_text(text, max_bytes=max_bytes)
    return text


def syntax_check(path: str, text: str) -> tuple[SyntaxStatus, str]:
    """Parse (never execute) formats ASE understands. Everything else is not checked."""
    suffix = PurePosixPath(path).suffix.lower()
    try:
        if suffix == ".py":
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ast.parse(text)
        elif suffix == ".json":
            json.loads(text)
        elif suffix == ".toml":
            tomllib.loads(text)
        else:
            return "not_checked", ""
    except SyntaxError as exc:
        return "failed", f"{exc.msg} (line {exc.lineno})"
    except (ValueError, RecursionError, MemoryError) as exc:  # JSON/TOML errors are ValueErrors
        return "failed", str(exc)[:200]
    return "passed", ""


def unified_diff(path: str, old: str | None, new: str) -> tuple[str, int, int]:
    """Return ``(diff text, added lines, removed lines)``. ``old=None`` means a new file."""
    old_lines = [] if old is None else old.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            old_lines,
            new.splitlines(keepends=True),
            fromfile="/dev/null" if old is None else f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    text = "".join(d if d.endswith("\n") else d + "\n" for d in diff)
    if len(text) > DIFF_CAP_CHARS:
        text = text[:DIFF_CAP_CHARS] + "\n[diff truncated]\n"
    return text, added, removed
