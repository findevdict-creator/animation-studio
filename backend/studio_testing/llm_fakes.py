"""Fake provider SDK clients (ported from tests/fakes.py in the old stack). They record every
request and return canned replies, so provider adapters and AI tasks are tested offline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeUsage:
    input_tokens: int = 1000
    output_tokens: int = 500
    cache_read_input_tokens: int = 0


@dataclass
class FakeResponse:
    parsed_output: Any
    stop_reason: str = "end_turn"
    stop_details: Any = None
    usage: FakeUsage = field(default_factory=FakeUsage)


class FakeMessages:
    """Returns responses in order; the last one repeats. Keys responses by the agent's
    output_format when given a dict, so one client can serve several agents."""

    def __init__(self, responses):
        self.by_model = responses if isinstance(responses, dict) else None
        self.responses = [] if self.by_model is not None else list(responses)
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.by_model is not None:
            return self.by_model[kwargs["output_format"]]
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def for_agent(self, output_format) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["output_format"] is output_format]


class FakeClient:
    def __init__(self, *responses, **by_schema):
        if by_schema:
            self.messages = FakeMessages(by_schema["by_schema"])
        else:
            self.messages = FakeMessages(responses)


# ---------------------------------------------------------------- other providers
def _text(reply) -> str:
    """Replies may be given as text, a dict or a Pydantic model; the fakes return JSON text."""
    if isinstance(reply, str):
        return reply
    if hasattr(reply, "model_dump_json"):
        return reply.model_dump_json()
    import json

    return json.dumps(reply)


class _Replies:
    """Replies in order; the last one repeats. A reply is text/dict/model, or a
    ``(reply, finish_reason)`` tuple."""

    def __init__(self, replies):
        self.replies = list(replies) or ["{}"]
        self.calls: list[dict[str, Any]] = []

    def next(self, kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return reply if isinstance(reply, tuple) else (reply, None)


class _Obj:
    """Attribute bag standing in for SDK response objects."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeOpenAICompletions(_Replies):
    def create(self, **kwargs):
        reply, finish = self.next(kwargs)
        refusal = None
        if finish == "refusal":
            refusal, finish, content = _text(reply), "stop", None
        else:
            content = _text(reply)
        message = _Obj(content=content, refusal=refusal)
        usage = _Obj(prompt_tokens=900, completion_tokens=400, prompt_tokens_details=_Obj(cached_tokens=100))
        return _Obj(choices=[_Obj(message=message, finish_reason=finish or "stop")], usage=usage)


class FakeOpenAIClient:
    """Stands in for ``openai.OpenAI`` (and DeepSeek / OpenAI-compatible endpoints):
    ``client.chat.completions.create`` records each request and returns JSON text."""

    def __init__(self, *replies):
        self.completions = FakeOpenAICompletions(replies)
        self.chat = _Obj(completions=self.completions)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.completions.calls


class FakeGeminiModels(_Replies):
    def generate_content(self, **kwargs):
        reply, finish = self.next(kwargs)
        usage = _Obj(prompt_token_count=800, candidates_token_count=300, thoughts_token_count=50, cached_content_token_count=0)
        text = "" if finish == "SAFETY" else _text(reply)
        return _Obj(text=text, candidates=[_Obj(finish_reason=finish or "STOP")], usage_metadata=usage, prompt_feedback=None)


class FakeGeminiClient:
    """Stands in for ``google.genai.Client``: ``client.models.generate_content``."""

    def __init__(self, *replies):
        self.models = FakeGeminiModels(replies)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.models.calls


def fake_client_for(provider: str, *replies):
    """A fake SDK client for ``provider`` that returns ``replies`` (models, dicts or text)."""
    if provider == "anthropic":
        return FakeClient(*[r if isinstance(r, FakeResponse) else FakeResponse(r) for r in replies])
    if provider == "gemini":
        return FakeGeminiClient(*replies)
    return FakeOpenAIClient(*replies)
