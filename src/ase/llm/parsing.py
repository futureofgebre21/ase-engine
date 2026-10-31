from __future__ import annotations

import re
from typing import TypeVar

from pydantic import BaseModel

from ase.core.errors import LLMResponseError
from ase.llm.schemas import RawAudit, RawPlan

_MAX_RESPONSE_CHARS = 200_000
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)

T = TypeVar("T", bound=BaseModel)


def parse_json_model(text: str, model: type[T]) -> T:
    """Extract and validate the JSON object in a model reply. Raises ``LLMResponseError``."""
    if not text or len(text) > _MAX_RESPONSE_CHARS:
        raise LLMResponseError("Model response was empty or too large.")
    body = _FENCE.sub("", _THINK.sub("", text).strip())
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise LLMResponseError("Model response contained no JSON object.")
    try:
        return model.model_validate_json(body[start : end + 1])
    except ValueError as exc:  # pydantic.ValidationError is a ValueError
        raise LLMResponseError("Model response did not match the required structure.") from exc


def parse_audit_json(text: str) -> RawAudit:
    return parse_json_model(text, RawAudit)


def parse_plan_json(text: str) -> RawPlan:
    return parse_json_model(text, RawPlan)
