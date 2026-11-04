"""Structured output for providers whose JSON-schema enforcement is weaker than Claude's.

The strategy is the same for every such provider: put the JSON schema in the system prompt,
turn on the provider's JSON mode (or its native schema option where it is reliable), pull
the JSON object out of the reply, validate it with the task's Pydantic model, and on a
failure send the errors back for a bounded number of repair turns. Whatever still fails
is raised as a ``ValidationError`` for the task to report.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from app.ai.providers.types import LLMError

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def json_schema_for(model: type[BaseModel]) -> dict:
    return model.model_json_schema()


def schema_instructions(model: type[BaseModel]) -> str:
    schema = json.dumps(json_schema_for(model), separators=(",", ":"))
    return (
        "\n\n## Output format\n"
        f"Reply with one JSON object that validates against this JSON schema for `{model.__name__}`. "
        "Output only the JSON: no prose before or after it and no Markdown fences. Use the exact "
        "field names, include every required field, and do not add fields the schema does not "
        f"define.\n\nJSON schema:\n{schema}"
    )


def extract_json(text: str) -> str:
    """The JSON object inside a reply: the whole reply, a fenced block, or the outermost braces."""
    text = (text or "").strip()
    if not text:
        raise LLMError("the model returned an empty reply")
    if text.startswith("{"):
        return text
    fenced = _FENCE.search(text)
    if fenced and fenced.group(1).strip().startswith("{"):
        return fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("the reply holds no JSON object")
    return text[start : end + 1]


def parse_reply(text: str, model: type[BaseModel]) -> BaseModel:
    """Validate a reply against ``model``. Raises ``ValidationError`` (bad shape or bad JSON)
    or ``LLMError`` (no JSON at all)."""
    return model.model_validate_json(extract_json(text))


def repair_message(error: Exception) -> str:
    if isinstance(error, ValidationError):
        lines = [f"- {'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in error.errors()[:40]]
        detail = "\n".join(lines)
    else:
        detail = f"- {error}"
    return (
        "Your reply did not validate against the schema:\n"
        f"{detail}\n\nReturn the complete corrected JSON object only."
    )
