"""Gemini through the ``google-genai`` SDK.

Structured output: JSON mode (``response_mime_type="application/json"``) with the schema in
the system prompt, validated and repaired with Pydantic. ``STUDIO_GEMINI_SCHEMA=native``
also passes the schema as ``response_json_schema``; it is off by default because Gemini
accepts only part of JSON Schema and our models use unions and free-form dicts.
"""

from __future__ import annotations

import os
from typing import Any

from studio.llm.base import JSONTextProvider
from studio.llm.registry import register_provider
from studio.llm.structured import json_schema_for
from studio.llm.types import LLMConfigError, LLMRequest

_BLOCKED = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "RECITATION"}


def _reason(value: Any) -> str:
    return str(getattr(value, "name", value) or "").upper().removeprefix("FINISHREASON.")


class GeminiProvider(JSONTextProvider):
    name = "gemini"
    default_model = "gemini-2.5-pro"
    api_key_env = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

    def make_client(self) -> Any:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise LLMConfigError("gemini: pip install google-genai") from exc
        return genai.Client(api_key=self.require_key())

    def contents(self, request: LLMRequest, turns: list[dict[str, str]]) -> list[dict[str, Any]]:
        first: list[dict[str, Any]] = [
            {"inline_data": {"mime_type": img.media_type, "data": img.b64}} for img in request.images
        ]
        first.append({"text": request.user})
        contents = [{"role": "user", "parts": first}]
        for t in turns:
            contents.append({"role": "model" if t["role"] == "assistant" else "user", "parts": [{"text": t["content"]}]})
        return contents

    def generation_config(self, request: LLMRequest, system: str) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "system_instruction": system,
            "max_output_tokens": request.max_tokens,
            "response_mime_type": "application/json",
        }
        if os.environ.get("STUDIO_GEMINI_SCHEMA") == "native":
            cfg["response_json_schema"] = json_schema_for(request.output_model)
        return cfg

    def _generate(self, request: LLMRequest, system: str, turns: list[dict[str, str]]) -> tuple[str, str, dict[str, int]]:
        response = self.client.models.generate_content(
            model=request.model, contents=self.contents(request, turns), config=self.generation_config(request, system)
        )
        usage: dict[str, int] = {}
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            usage["input_tokens"] = getattr(meta, "prompt_token_count", 0) or 0
            usage["output_tokens"] = (getattr(meta, "candidates_token_count", 0) or 0) + (getattr(meta, "thoughts_token_count", 0) or 0)
            usage["cache_read_input_tokens"] = getattr(meta, "cached_content_token_count", 0) or 0
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            return "", "refusal", usage
        candidates = getattr(response, "candidates", None) or []
        reason = _reason(getattr(candidates[0], "finish_reason", "")) if candidates else ""
        if reason in _BLOCKED:
            return "", "refusal", usage
        text = getattr(response, "text", None) or ""
        if reason == "MAX_TOKENS":
            return text, "max_tokens", usage
        return text, "ok", usage


register_provider(GeminiProvider, aliases=("google",))
