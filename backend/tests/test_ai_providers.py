"""The provider layer ported from ``studio/llm``: registry, per-task choice, request shapes,
JSON repair, refusals. Every provider runs against a fake SDK client: no keys, no network."""

import pytest
from pydantic import BaseModel

from app.ai.providers import LLMConfigError, LLMRequest, LLMSettings, parse_choice, provider_names, resolve_name
from app.ai.providers.structured import extract_json
from studio_testing.llm_fakes import FakeGeminiClient, FakeOpenAIClient, FakeResponse, fake_client_for

ENV = ("STUDIO_LLM_PROVIDER", "STUDIO_MODEL", "STUDIO_ANTHROPIC_MODEL", "STUDIO_OPENAI_MODEL", "STUDIO_GEMINI_MODEL",
       "STUDIO_OPENAI_REASONING", "STUDIO_GEMINI_SCHEMA")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ENV:
        monkeypatch.delenv(var, raising=False)


class Answer(BaseModel):
    color: str
    count: int


def req(**kw):
    return LLMRequest(model=kw.pop("model", "m"), system="sys", user="hi", output_model=Answer, **kw)


def test_registry_and_choice_precedence():
    assert {"anthropic", "openai", "gemini", "deepseek", "openai_compatible"} <= set(provider_names())
    assert resolve_name("claude") == "anthropic" and parse_choice("openai:gpt-5") == ("openai", "gpt-5")
    with pytest.raises(LLMConfigError):
        resolve_name("nope")
    s = LLMSettings(provider="anthropic", model="claude-x", tasks={"interpret_character": "openai"})
    assert s.choice_for("other") == ("anthropic", "claude-x")
    assert s.choice_for("interpret_character")[0] == "openai"
    assert s.choice_for("interpret_character", "gemini:gemini-z") == ("gemini", "gemini-z")
    assert LLMSettings().choice_for("x") == ("anthropic", "claude-opus-5-5")


def test_anthropic_uses_native_structured_output():
    client = fake_client_for("anthropic", Answer(color="red", count=2))
    s = LLMSettings(clients={"anthropic": client})
    provider, model = s.for_task("t")
    result = provider.complete(req(model=model))
    assert result.parsed == Answer(color="red", count=2) and result.stop == "ok"
    call = client.messages.calls[0]
    assert call["output_format"] is Answer and call["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_refusal():
    client = fake_client_for("anthropic", FakeResponse(None, stop_reason="refusal"))
    provider = LLMSettings(clients={"anthropic": client}).provider_for("anthropic")
    assert provider.complete(req()).stop == "refusal"


def test_openai_bad_json_is_repaired_once():
    client = FakeOpenAIClient("not json at all", '{"color": "blue", "count": 3}')
    provider = LLMSettings(clients={"openai": client}).provider_for("openai")
    result = provider.complete(req(json_repairs=1))
    assert result.parsed == Answer(color="blue", count=3) and result.calls == 2
    assert client.calls[1]["messages"][-1]["role"] == "user" and "did not validate" in client.calls[1]["messages"][-1]["content"]


def test_gemini_safety_block():
    provider = LLMSettings(clients={"gemini": FakeGeminiClient(("{}", "SAFETY"))}).provider_for("gemini")
    assert provider.complete(req()).stop == "refusal"


def test_extract_json():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Sure: {"a": 1} done') == '{"a": 1}'
