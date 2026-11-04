"""Claude through the Anthropic SDK, with native structured outputs
(``client.messages.parse(output_format=Model)``) and a cached system prompt."""

from __future__ import annotations

import os
from typing import Any

from studio.llm.base import LLMProvider
from studio.llm.registry import register_provider
from studio.llm.types import LLMConfigError, LLMRequest, LLMResult


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    default_model = "claude-opus-5-5"
    api_key_env = ("ANTHROPIC_API_KEY",)

    @classmethod
    def resolve_default_model(cls) -> str:
        # STUDIO_MODEL predates multi-provider support and still sets the Claude default.
        return os.environ.get(cls.model_env()) or os.environ.get("STUDIO_MODEL") or cls.default_model

    def make_client(self) -> Any:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMConfigError("anthropic: pip install anthropic") from exc
        kwargs: dict[str, Any] = {}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return anthropic.Anthropic(**kwargs)

    @staticmethod
    def user_content(request: LLMRequest) -> Any:
        if not request.images:
            return request.user
        blocks: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "base64", "media_type": img.media_type, "data": img.b64}}
            for img in request.images
        ]
        blocks.append({"type": "text", "text": request.user})
        return blocks

    def complete(self, request: LLMRequest) -> LLMResult:
        self.check(request)
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
            "output_format": request.output_model,
            "messages": [{"role": "user", "content": self.user_content(request)}],
        }
        if request.effort:
            kwargs["output_config"] = {"effort": request.effort}
        response = self.client.messages.parse(**kwargs)
        usage: dict[str, int] = {}
        u = getattr(response, "usage", None)
        if u is not None:
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
                usage[key] = getattr(u, key, 0) or 0
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            return LLMResult(None, stop="refusal", detail=str(getattr(details, "category", None)), usage=usage)
        if response.stop_reason == "max_tokens":
            return LLMResult(None, stop="max_tokens", usage=usage)
        return LLMResult(response.parsed_output, usage=usage)


register_provider(AnthropicProvider, aliases=("claude",))
