"""Module 16: LLM providers behind the agents. Every provider is driven through a fake SDK
client (no keys, no network): request shape, structured-output strategy, JSON repair,
refusal and truncation mapping, per-agent provider choice, the CLI and the GUI."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from studio.agents import build_agent, llm_registry
from studio.agents.base import AgentRefused
from studio.agents.story_analyst import StoryAnalyst
from studio.agents.stubs import FIXTURE_DIR, stub_registry
from studio.coordinator import AgentContext, Coordinator, RunStore
from studio.coordinator.pipeline import load_manifest
from studio.llm import (
    LLMConfigError,
    LLMSettings,
    describe_providers,
    parse_choice,
    provider_names,
    register_provider,
    resolve_name,
)
from studio.llm.providers.openai import OpenAIProvider
from studio.llm.structured import extract_json
from studio.schemas import Config, StoryBible
from studio.schemas.config import Flags
from tests.fakes import FakeGeminiClient, FakeOpenAIClient, fake_client_for

PROVIDER_ENV = (
    "STUDIO_LLM_PROVIDER", "STUDIO_MODEL", "STUDIO_ANTHROPIC_MODEL", "STUDIO_OPENAI_MODEL", "STUDIO_GEMINI_MODEL",
    "STUDIO_DEEPSEEK_MODEL", "STUDIO_OPENAI_REASONING", "STUDIO_DEEPSEEK_MAX_TOKENS", "STUDIO_GEMINI_SCHEMA",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in PROVIDER_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def bible():
    return StoryBible.model_validate_json((FIXTURE_DIR / "story_bible.json").read_text())


@pytest.fixture
def config():
    return Config(story_text=(FIXTURE_DIR / "story.txt").read_text(), target_duration_sec=30, human_approval={"mode": "none"})


def _ctx(config, **kw):
    return AgentContext(config=config, manifest=load_manifest(FIXTURE_DIR / "manifest.json"), **kw)


# ------------------------------------------------------------------ registry
def test_builtin_providers_and_aliases():
    assert {"anthropic", "openai", "gemini", "deepseek", "openai_compatible"} <= set(provider_names())
    assert resolve_name("Claude") == "anthropic" and resolve_name("google") == "gemini" and resolve_name("gpt") == "openai"
    assert parse_choice("openai") == ("openai", None)
    assert parse_choice("claude:claude-sonnet-5-5") == ("anthropic", "claude-sonnet-5-5")
    assert parse_choice("openai_compatible:llama3:8b") == ("openai_compatible", "llama3:8b")
    with pytest.raises(LLMConfigError, match="unknown LLM provider"):
        resolve_name("nope")
    rows = {r["name"]: r for r in describe_providers()}
    assert rows["deepseek"]["supports_images"] is False and rows["gemini"]["api_key_env"] == ["GEMINI_API_KEY", "GOOGLE_API_KEY"]


def test_a_new_provider_is_one_class(config, bible):
    class MistralProvider(OpenAIProvider):
        name = "mistral_test"
        default_model = "mistral-large-latest"
        api_key_env = ("MISTRAL_API_KEY",)
        default_base_url = "https://api.mistral.ai/v1"
        response_mode = "json_object"

    register_provider(MistralProvider)
    flags = Flags(llm_provider="mistral_test")
    client = FakeOpenAIClient(bible)
    settings = LLMSettings.from_flags(flags, clients={"mistral_test": client})
    agent = build_agent(StoryAnalyst, settings)
    assert agent.llm_label == "mistral_test:mistral-large-latest"
    assert agent.run(_ctx(config)).title == "The Lantern Fox"
    assert client.calls[0]["response_format"] == {"type": "json_object"}


# ------------------------------------------------------------------ flags
def test_flags_validate_provider_choices():
    f = Flags(llm_provider="Claude", llm_model="  ", llm_agents={"director": "google", "layout_td": "gpt:gpt-5-mini", "scene_builder": ""})
    assert f.llm_provider == "anthropic" and f.llm_model is None
    assert f.llm_agents == {"director": "gemini", "layout_td": "openai:gpt-5-mini"}
    with pytest.raises(ValidationError, match="unknown LLM provider"):
        Flags(llm_provider="nope")
    with pytest.raises(ValidationError, match="unknown agent"):
        Flags(llm_agents={"narrator": "openai"})
    with pytest.raises(ValidationError, match="llm_agents.director"):
        Flags(llm_agents={"director": "nope:x"})


def test_llm_flags_stay_out_of_the_prompt(config):
    cfg = config.model_copy(update={"flags": Flags(llm_provider="openai", llm_model="gpt-5")})
    msg = StoryAnalyst(client=object()).build_user_message(_ctx(cfg))
    assert "llm_provider" not in msg and "gpt-5" not in msg and "library_mode" in msg


def test_settings_precedence(monkeypatch):
    assert LLMSettings().choice_for("director") == ("anthropic", "claude-opus-5-5")
    monkeypatch.setenv("STUDIO_MODEL", "claude-legacy")  # the pre-module-16 Claude override still works
    assert LLMSettings().choice_for("director") == ("anthropic", "claude-legacy")
    monkeypatch.setenv("STUDIO_LLM_PROVIDER", "deepseek")
    assert LLMSettings().choice_for("director") == ("deepseek", "deepseek-chat")
    monkeypatch.setenv("STUDIO_OPENAI_MODEL", "gpt-env")
    s = LLMSettings(provider="openai", model="gpt-run", agents={"director": "gemini", "layout_td": "openai", "story_analyst": "openai:gpt-pin"})
    assert s.choice_for("scene_builder") == ("openai", "gpt-run")
    assert s.choice_for("layout_td") == ("openai", "gpt-run")  # same provider, no model: the run model
    assert s.choice_for("story_analyst") == ("openai", "gpt-pin")
    assert s.choice_for("director") == ("gemini", "gemini-2.5-pro")
    assert LLMSettings(provider="openai").choice_for("director") == ("openai", "gpt-env")
    with pytest.raises(LLMConfigError, match="no default model"):
        LLMSettings(provider="openai_compatible").choice_for("director")


def test_registry_mixes_providers_per_agent(config):
    flags = Flags(llm_provider="openai", llm_model="gpt-5", llm_agents={"director": "gemini", "continuity_checker": "claude:claude-sonnet-5-5"})
    cfg = config.model_copy(update={"flags": flags})
    reg = llm_registry(cfg)
    labels = {n: reg.get(n).llm_label for n in ("story_analyst", "director", "continuity_checker")}
    assert labels == {"story_analyst": "openai:gpt-5", "director": "gemini:gemini-2.5-pro", "continuity_checker": "anthropic:claude-sonnet-5-5"}
    # one adapter (and SDK client) per provider, shared by its agents
    assert reg.get("story_analyst").provider is reg.get("scene_builder").provider


# ------------------------------------------------------------------ openai
def test_openai_request_shape_and_usage(config, bible):
    client = FakeOpenAIClient(bible)
    agent = StoryAnalyst(provider=OpenAIProvider(client=client), model="gpt-5")
    assert agent.run(_ctx(config)).title == "The Lantern Fox"
    call = client.calls[0]
    assert call["model"] == "gpt-5" and call["max_completion_tokens"] == agent.max_tokens
    assert call["reasoning_effort"] == "high"
    fmt = call["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is False and fmt["json_schema"]["name"] == "StoryBible"
    system, user = call["messages"][0], call["messages"][1]
    assert system["role"] == "system" and "Output format" in system["content"] and '"beats"' in system["content"]
    assert user["role"] == "user" and "glowing fox" in user["content"]
    assert agent.last_usage == {"input_tokens": 900, "output_tokens": 400, "cache_read_input_tokens": 100}


def test_openai_non_reasoning_model_gets_no_effort(config, bible):
    client = FakeOpenAIClient(bible)
    StoryAnalyst(provider=OpenAIProvider(client=client), model="gpt-4.1").run(_ctx(config))
    assert "reasoning_effort" not in client.calls[0]


def test_bad_json_is_repaired_once(config, bible):
    broken = bible.model_dump(mode="json")
    del broken["beats"]
    client = FakeOpenAIClient("Sure! Here it is:\n```json\n" + json.dumps(broken) + "\n```", bible)
    agent = StoryAnalyst(provider=OpenAIProvider(client=client), model="gpt-5")
    assert agent.run(_ctx(config)).title == "The Lantern Fox"
    assert len(client.calls) == 2
    msgs = client.calls[1]["messages"]
    assert msgs[2]["role"] == "assistant" and msgs[3]["role"] == "user"
    assert "beats: Field required" in msgs[3]["content"]
    assert agent.last_usage["input_tokens"] == 1800


def test_unrepaired_output_becomes_coordinator_feedback(tmp_path, config, bible):
    """Repairs exhausted: the ValidationError reaches the coordinator, which retries the
    agent with the schema errors as feedback, as it does for Claude."""
    client = FakeOpenAIClient("{\"title\": \"x\"}", "{\"title\": \"x\"}", bible)
    agent = StoryAnalyst(provider=OpenAIProvider(client=client), model="gpt-5", json_repairs=1)
    with pytest.raises(ValidationError):
        agent.run(_ctx(config))
    client = FakeOpenAIClient("{\"title\": \"x\"}", "not json at all", bible)
    registry = stub_registry()
    registry.register(StoryAnalyst(provider=OpenAIProvider(client=client), model="gpt-5", json_repairs=1))
    store = RunStore(tmp_path / "run")
    Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, store).run()
    assert store.load_output("story_analyst").title == "The Lantern Fox"
    assert len(client.calls) == 3
    assert "Problems with your previous attempt" in client.calls[2]["messages"][1]["content"]


def test_openai_refusal_and_truncation(config):
    client = FakeOpenAIClient(("I can't help with that.", "refusal"))
    with pytest.raises(AgentRefused):
        StoryAnalyst(provider=OpenAIProvider(client=client), model="gpt-5").run(_ctx(config))
    client = FakeOpenAIClient(('{"title": "The Lan', "length"))
    with pytest.raises(RuntimeError, match="truncated"):
        StoryAnalyst(provider=OpenAIProvider(client=client), model="gpt-5").run(_ctx(config))


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == '{"a": 1}'
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Here: {"a": {"b": 2}} done') == '{"a": {"b": 2}}'


# ------------------------------------------------------------------ deepseek
def test_deepseek_uses_json_mode_and_caps_tokens(config, bible, tmp_path):
    from studio.agents.asset_builder import AssetBuilder
    from studio.agents.clip_tagger import ClipTagger
    from studio.llm import create_provider

    client = fake_client_for("deepseek", bible)
    agent = StoryAnalyst(provider=create_provider("deepseek", client=client))
    assert agent.model == "deepseek-chat"
    agent.run(_ctx(config))
    call = client.calls[0]
    assert call["response_format"] == {"type": "json_object"} and "reasoning_effort" not in call
    assert call["max_tokens"] == 8192 and "max_completion_tokens" not in call  # 16000 asked, capped
    assert "json" in call["messages"][0]["content"].lower()  # DeepSeek's JSON mode wants the word in the prompt
    assert AssetBuilder.max_tokens > 8192

    sheet = tmp_path / "sheet.png"
    sheet.write_bytes(b"\x89PNG\r\n\x1a\n")
    tags = ClipTagger(provider=create_provider("deepseek", client=FakeOpenAIClient())).tag(sheet, "clip_x", known_tags=["gesture"])
    assert tags.tags == ["gesture"] and tags.confidence == 0.0 and "cannot read images" in tags.notes


# ------------------------------------------------------------------ gemini
def test_gemini_request_shape(config, bible):
    from studio.llm import create_provider

    client = FakeGeminiClient(bible)
    agent = StoryAnalyst(provider=create_provider("gemini", client=client))
    assert agent.run(_ctx(config)).title == "The Lantern Fox"
    call = client.calls[0]
    assert call["model"] == "gemini-2.5-pro"
    cfg = call["config"]
    assert cfg["response_mime_type"] == "application/json" and "response_json_schema" not in cfg
    assert "Output format" in cfg["system_instruction"] and cfg["max_output_tokens"] == agent.max_tokens
    assert call["contents"][0]["role"] == "user" and "glowing fox" in call["contents"][0]["parts"][-1]["text"]
    assert agent.last_usage == {"input_tokens": 800, "output_tokens": 350, "cache_read_input_tokens": 0}


def test_gemini_native_schema_and_repair_turns(monkeypatch, config, bible):
    from studio.llm import create_provider

    monkeypatch.setenv("STUDIO_GEMINI_SCHEMA", "native")
    client = FakeGeminiClient("{}", bible)
    StoryAnalyst(provider=create_provider("gemini", client=client)).run(_ctx(config))
    assert client.calls[0]["config"]["response_json_schema"]["title"] == "StoryBible"
    roles = [c["role"] for c in client.calls[1]["contents"]]
    assert roles == ["user", "model", "user"]


def test_gemini_safety_block_and_max_tokens(config):
    from studio.llm import create_provider

    with pytest.raises(AgentRefused):
        StoryAnalyst(provider=create_provider("gemini", client=FakeGeminiClient(("", "SAFETY")))).run(_ctx(config))
    with pytest.raises(RuntimeError, match="truncated"):
        StoryAnalyst(provider=create_provider("gemini", client=FakeGeminiClient(('{"ti', "MAX_TOKENS")))).run(_ctx(config))


# ------------------------------------------------------------------ vision agents
@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_render_qa_sends_the_image_on_every_vision_provider(tmp_path, provider):
    from studio.agents.render_qa import QAVerdict, RenderQA
    from studio.llm import create_provider
    from studio.schemas.build_requests import BuildRequest

    sheet = tmp_path / "s.png"
    sheet.write_bytes(b"\x89PNG\r\n\x1a\n")
    client = fake_client_for(provider, QAVerdict(score=0.9, problems=[], notes="clean"))
    qa = RenderQA(provider=create_provider(provider, client=client), model="m")
    request = BuildRequest(id="br_prop_x", kind="prop", for_id="prop_x", spec="a small wooden box", bounds_m=(0.3, 0.3, 0.3))
    verdict = qa.review(sheet, request, {"polys": 10}, Config(story_text="x", target_duration_sec=1))
    assert verdict.score == 0.9
    if provider == "anthropic":
        first = client.messages.calls[0]["messages"][0]["content"][0]
        assert first["type"] == "image"
    elif provider == "openai":
        first = client.calls[0]["messages"][1]["content"][0]
        assert first["type"] == "image_url" and first["image_url"]["url"].startswith("data:image/png;base64,")
    else:
        first = client.calls[0]["contents"][0]["parts"][0]
        assert first["inline_data"]["mime_type"] == "image/png"


# ------------------------------------------------------------------ pipeline, CLI, GUI
def test_full_pipeline_on_a_non_claude_provider(tmp_path, config, bible):
    """story_analyst on OpenAI inside the coordinator, stubs for the rest."""
    client = FakeOpenAIClient(bible)
    cfg = config.model_copy(update={"flags": Flags(llm_provider="openai", llm_model="gpt-5")})
    settings = LLMSettings.from_flags(cfg.flags, clients={"openai": client})
    registry = stub_registry()
    registry.register(build_agent(StoryAnalyst, settings))
    store = RunStore(tmp_path / "run")
    path = Coordinator(cfg, load_manifest(FIXTURE_DIR / "manifest.json"), registry, store).run()
    assert path.exists() and len(client.calls) == 1


def test_cli_llm_options(tmp_path, capsys):
    import argparse

    from studio.cli import apply_llm_args, build_registry, main

    cfg = Config(story_text="x", target_duration_sec=1, flags={"llm_agents": {"director": "gemini"}})
    args = argparse.Namespace(llm_provider="deepseek", llm_model=None, llm_agent=["layout_td=openai:gpt-5"])
    out = apply_llm_args(cfg, args)
    assert out.flags.llm_provider == "deepseek" and out.flags.llm_agents == {"director": "gemini", "layout_td": "openai:gpt-5"}
    with pytest.raises(SystemExit, match="bad LLM option"):
        apply_llm_args(cfg, argparse.Namespace(llm_provider="nope", llm_model=None, llm_agent=[]))
    with pytest.raises(SystemExit, match="AGENT=PROVIDER"):
        apply_llm_args(cfg, argparse.Namespace(llm_provider=None, llm_model=None, llm_agent=["director"]))
    assert build_registry("claude", out).get("director").llm_label == "gemini:gemini-2.5-pro"
    assert build_registry("llm", out).get("story_analyst").llm_label == "deepseek:deepseek-chat"
    assert main(["providers"]) == 0
    printed = capsys.readouterr().out
    assert "openai" in printed and "deepseek" in printed and "no images" in printed

    rc = main(["run", "--story", str(FIXTURE_DIR / "story.txt"), "--run-dir", str(tmp_path / "r"), "--agents", "stub",
               "--duration", "30", "--llm-provider", "gemini", "--llm-agent", "director=openai"])
    assert rc in (0, 2)
    saved = RunStore(tmp_path / "r").load_config()
    assert saved.flags.llm_provider == "gemini" and saved.flags.llm_agents == {"director": "openai"}


def test_gui_exposes_and_saves_provider_choice(tmp_path):
    from pathlib import Path

    from studio.gui import StudioService

    repo = Path(__file__).resolve().parent.parent
    svc = StudioService(runs_dir=tmp_path / "runs", library=repo / "library", agents="stub", allowed_agents=("claude", "stub"))
    try:
        assert svc.allowed_agents == ("llm", "stub")
        info = svc.info()
        assert {p["name"] for p in info["llm_providers"]} >= {"anthropic", "openai", "gemini", "deepseek"}
        assert info["llm_default_provider"] == "anthropic" and "director" in info["llm_agents"]
        body = {
            "name": "prov", "agents": "stub", "story_text": (FIXTURE_DIR / "story.txt").read_text(),
            "config": {
                "target_duration_sec": 30, "library_manifest_path": str(FIXTURE_DIR / "manifest.json"),
                "human_approval": {"mode": "none"},
                "flags": {"llm_provider": "openai", "llm_model": "gpt-5", "llm_agents": {"director": "gemini"}},
            },
        }
        svc.create_run(body)
        assert svc.wait("prov") == "done"
        detail = svc.run_detail("prov")
        assert detail["config"]["flags"]["llm_provider"] == "openai"
        assert detail["llm"]["story_analyst"] == "openai:gpt-5" and detail["llm"]["director"] == "gemini:gemini-2.5-pro"
        bad = dict(body, name="bad", config=dict(body["config"], flags={"llm_provider": "nope"}))
        from studio.gui import GuiError

        with pytest.raises(GuiError):
            svc.create_run(bad)
    finally:
        svc.close()
