"""OpenAI, DeepSeek and any OpenAI-compatible endpoint through the ``openai`` SDK.

Structured output: OpenAI gets the schema through ``response_format`` json_schema in
non-strict mode (strict mode rejects the optional fields and defaults our Pydantic models
use) and also in the system prompt; DeepSeek and generic endpoints get JSON mode
(``json_object``) plus the schema in the prompt. Either way the reply is validated with
Pydantic and repaired, see ``app.ai.providers.structured``.
"""

from __future__ import annotations

import os
import re
from typing import Any

from app.ai.providers.base import JSONTextProvider
from app.ai.providers.registry import register_provider
from app.ai.providers.structured import json_schema_for
from app.ai.providers.types import LLMConfigError, LLMRequest

_REASONING_MODEL = re.compile(r"^(o\d|gpt-5)", re.IGNORECASE)
_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high", "minimal": "minimal"}


class OpenAIProvider(JSONTextProvider):
    name = "openai"
    default_model = "gpt-5"
    api_key_env = ("OPENAI_API_KEY",)
    base_url_env = "OPENAI_BASE_URL"
    default_base_url: str | None = None
    #: "json_schema" (native, non-strict), "json_object" (JSON mode) or "none"
    response_mode = "json_schema"
    #: name of the max-tokens parameter the endpoint accepts
    max_tokens_param = "max_completion_tokens"
    #: output-token cap for the endpoint, 0 for none (env STUDIO_<NAME>_MAX_TOKENS)
    max_tokens_cap = 0

    def make_client(self) -> Any:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover
            raise LLMConfigError(f"{self.name}: pip install openai") from exc
        base_url = self.base_url or os.environ.get(self.base_url_env) or self.default_base_url
        return openai.OpenAI(api_key=self.require_key(), **({"base_url": base_url} if base_url else {}))

    def cap(self) -> int:
        return int(os.environ.get(f"STUDIO_{self.name.upper()}_MAX_TOKENS") or self.max_tokens_cap or 0)

    def reasoning_effort(self, request: LLMRequest) -> str | None:
        flag = os.environ.get(f"STUDIO_{self.name.upper()}_REASONING")
        on = flag == "1" if flag in ("0", "1") else bool(_REASONING_MODEL.match(request.model))
        return _EFFORT.get(request.effort or "") if on else None

    def user_content(self, request: LLMRequest) -> Any:
        if not request.images:
            return request.user
        parts: list[dict[str, Any]] = [
            {"type": "image_url", "image_url": {"url": f"data:{img.media_type};base64,{img.b64}"}} for img in request.images
        ]
        parts.append({"type": "text", "text": request.user})
        return parts

    def response_format(self, request: LLMRequest) -> dict[str, Any] | None:
        if self.response_mode == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {"name": request.output_model.__name__, "schema": json_schema_for(request.output_model), "strict": False},
            }
        if self.response_mode == "json_object":
            return {"type": "json_object"}
        return None

    def _generate(self, request: LLMRequest, system: str, turns: list[dict[str, str]]) -> tuple[str, str, dict[str, int]]:
        max_tokens = request.max_tokens
        if self.cap():
            max_tokens = min(max_tokens, self.cap())
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": self.user_content(request)},
                *turns,
            ],
            self.max_tokens_param: max_tokens,
        }
        fmt = self.response_format(request)
        if fmt:
            kwargs["response_format"] = fmt
        effort = self.reasoning_effort(request)
        if effort:
            kwargs["reasoning_effort"] = effort
        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message
        usage: dict[str, int] = {}
        u = getattr(response, "usage", None)
        if u is not None:
            usage["input_tokens"] = getattr(u, "prompt_tokens", 0) or 0
            usage["output_tokens"] = getattr(u, "completion_tokens", 0) or 0
            details = getattr(u, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) if details is not None else getattr(u, "prompt_cache_hit_tokens", 0)
            usage["cache_read_input_tokens"] = cached or 0
        if getattr(message, "refusal", None):
            return message.refusal, "refusal", usage
        if choice.finish_reason == "length":
            return message.content or "", "max_tokens", usage
        if choice.finish_reason == "content_filter":
            return message.content or "", "refusal", usage
        return message.content or "", "ok", usage


class DeepSeekProvider(OpenAIProvider):
    name = "deepseek"
    default_model = "deepseek-chat"
    api_key_env = ("DEEPSEEK_API_KEY",)
    base_url_env = "DEEPSEEK_BASE_URL"
    default_base_url = "https://api.deepseek.com"
    response_mode = "json_object"  # DeepSeek has JSON mode but no schema enforcement
    max_tokens_param = "max_tokens"
    max_tokens_cap = 8192  # deepseek-chat's output limit; raise with STUDIO_DEEPSEEK_MAX_TOKENS
    supports_images = False

    def reasoning_effort(self, request: LLMRequest) -> str | None:
        return None


class OpenAICompatibleProvider(OpenAIProvider):
    """Any endpoint that speaks the OpenAI chat API (a local server, a gateway). Set
    STUDIO_OPENAI_COMPATIBLE_BASE_URL and, if it needs one, STUDIO_OPENAI_COMPATIBLE_API_KEY."""

    name = "openai_compatible"
    default_model = ""
    api_key_env = ("STUDIO_OPENAI_COMPATIBLE_API_KEY",)
    base_url_env = "STUDIO_OPENAI_COMPATIBLE_BASE_URL"
    response_mode = "json_object"
    max_tokens_param = "max_tokens"

    @classmethod
    def configured(cls) -> bool:
        return bool(os.environ.get(cls.base_url_env))

    @classmethod
    def setup_hint(cls) -> str:
        return f"{cls.base_url_env} (and {cls.model_env()} or a model name)"

    def require_key(self) -> str:
        return self.api_key or self.find_api_key() or "not-needed"

    def make_client(self) -> Any:
        if not (self.base_url or os.environ.get(self.base_url_env)):
            raise LLMConfigError(f"{self.name}: set {self.base_url_env} to the endpoint's /v1 URL")
        return super().make_client()

    def reasoning_effort(self, request: LLMRequest) -> str | None:
        return None


register_provider(OpenAIProvider, aliases=("gpt", "chatgpt"))
register_provider(DeepSeekProvider)
register_provider(OpenAICompatibleProvider, aliases=("openai-compatible", "compatible"))
