import pytest

from ase.agent.edits import (
    MAX_EDITS,
    apply_edits,
    check_generated_text,
    decode_text,
    encode_text,
    syntax_check,
    unified_diff,
)
from ase.core.errors import ChangeRejected
from ase.llm.schemas import RawEdit

LIMIT = 100_000
SRC = "def f(x):\n    return x + 1\n\n\ndef g(x):\n    return x + 2\n"


def _edit(old, new):
    return RawEdit(old=old, new=new)


def _rejected(original, edits):
    with pytest.raises(ChangeRejected) as info:
        apply_edits(original, edits, max_bytes=LIMIT)
    return info.value.message


def test_a_unique_edit_is_applied_exactly():
    out = apply_edits(SRC, [_edit("    return x + 1\n", "    return x + 10\n")], max_bytes=LIMIT)
    assert out == SRC.replace("x + 1\n", "x + 10\n", 1)


def test_edits_apply_in_order_and_see_earlier_results():
    edits = [_edit("return x + 1", "return x + 5"), _edit("return x + 5", "return x + 6")]
    assert "return x + 6" in apply_edits(SRC, edits, max_bytes=LIMIT)


def test_text_that_is_missing_or_ambiguous_is_rejected():
    assert "found 0 times" in _rejected(SRC, [_edit("return x + 99", "return 0")])
    assert "found 2 times" in _rejected(SRC, [_edit("    return x", "    return 0")])


def test_empty_identical_and_excessive_edits_are_rejected():
    assert "no edits" in _rejected(SRC, [])
    assert "empty" in _rejected(SRC, [_edit("   ", "x")])
    assert "identical" in _rejected(SRC, [_edit("return x + 1", "return x + 1")])
    many = [_edit(f"old{i}", f"new{i}") for i in range(MAX_EDITS + 1)]
    assert "too many" in _rejected(SRC, many)


def test_redacted_text_is_never_touched_or_produced():
    hidden = "KEY = [REDACTED]\n"
    assert "hidden from the model" in _rejected(hidden, [_edit("KEY = [REDACTED]", "KEY = 1")])
    assert "REDACTED" in _rejected(SRC, [_edit("return x + 1", "return '[REDACTED]'")])


def test_generated_secrets_are_rejected_but_ordinary_credential_variables_are_fine():
    token = "ghp_" + "a" * 36
    assert "secret-like" in _rejected(SRC, [_edit("return x + 1", f"return '{token}'")])
    ok = 'password = os.environ["APP_PASSWORD"]\napi_key = load_key()\n'
    check_generated_text(ok, max_bytes=LIMIT)  # normal code must not be flagged


def test_size_limit_applies_to_the_result():
    with pytest.raises(ChangeRejected) as info:
        apply_edits(SRC, [_edit("return x + 1", "return " + "9" * 500)], max_bytes=50)
    assert "larger than 50 bytes" in info.value.message


def test_crlf_bom_and_encoding_round_trip():
    raw = b"\xef\xbb\xbfa = 1\r\nb = 2\r\n"
    text, newline, bom = decode_text(raw)
    assert (text, newline, bom) == ("a = 1\nb = 2\n", "\r\n", True)
    assert encode_text(text, newline, bom) == raw
    text, newline, bom = decode_text(b"a = 1\nb = 2\n")
    assert newline == "\n" and not bom


def test_non_text_files_are_refused():
    for raw in (b"\xff\xfe\x00bad", b"a\x00b"):
        with pytest.raises(ChangeRejected):
            decode_text(raw)


def test_syntax_check_covers_only_formats_it_can_parse():
    assert syntax_check("a.py", "x = 1\n")[0] == "passed"
    assert syntax_check("a.py", "x = (\n")[0] == "failed"
    assert "line" in syntax_check("a.py", "def f(:\n")[1]
    assert syntax_check("a.json", '{"a": 1}')[0] == "passed"
    assert syntax_check("a.json", "{a: 1}")[0] == "failed"
    assert syntax_check("a.toml", "a = 1")[0] == "passed"
    assert syntax_check("a.toml", "a = ")[0] == "failed"
    for name in ("a.go", "a.js", "Dockerfile", "a.yaml", "a.md"):
        assert syntax_check(name, "anything (((")[0] == "not_checked", name  # never "passed"


def test_unified_diff_counts_and_marks_new_files():
    diff, added, removed = unified_diff("a.py", "x = 1\ny = 2\n", "x = 1\ny = 3\nz = 4\n")
    assert (added, removed) == (2, 1)
    assert "--- a/a.py" in diff and "+++ b/a.py" in diff and "-y = 2" in diff
    diff, added, removed = unified_diff("new.py", None, "a\nb\n")
    assert "--- /dev/null" in diff and (added, removed) == (2, 0)


def test_huge_diffs_are_truncated_with_a_notice():
    diff, _, _ = unified_diff("a.txt", None, "line\n" * 20_000)
    assert diff.endswith("[diff truncated]\n") and len(diff) < 41_000
