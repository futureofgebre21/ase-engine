import json

import pytest

from ase.core.errors import LLMResponseError
from ase.llm.parsing import parse_audit_json

GOOD = {
    "summary": "s",
    "findings": [
        {
            "severity": "high", "category": "bug", "description": "d",
            "evidence": [{"file_path": "a.py", "line_start": 1, "line_end": 2, "quote": "x"}],
            "recommended_fix": "f", "confidence": 0.9,
        }
    ],
}  # fmt: skip


def test_plain_json_parses():
    raw = parse_audit_json(json.dumps(GOOD))
    assert raw.findings[0].evidence[0].file_path == "a.py"


def test_fenced_json_with_chatter_parses():
    text = "Sure! Here you go:\n```json\n" + json.dumps(GOOD) + "\n```\nHope that helps."
    assert parse_audit_json(text).summary == "s"


def test_unknown_extra_keys_are_ignored():
    data = {**GOOD, "action": "push_to_main", "run_command": "rm -rf /"}
    raw = parse_audit_json(json.dumps(data))
    assert not hasattr(raw, "action") and not hasattr(raw, "run_command")


def test_malformed_responses_raise_response_error():
    bad = [
        "",
        "no json here",
        "{not json}",
        json.dumps({"summary": "s", "findings": "not a list"}),
        json.dumps({"summary": "s", "findings": [{"severity": "high"}]}),  # missing fields
        "x" * 300_000,
    ]
    for text in bad:
        with pytest.raises(LLMResponseError):
            parse_audit_json(text)


def test_reasoning_block_before_the_json_is_ignored():
    text = "<think>I should use {braces} while thinking</think>\n" + json.dumps(GOOD)
    assert parse_audit_json(text).summary == "s"
