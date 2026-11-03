"""Character Designer against the fake client and inside the pipeline."""

from __future__ import annotations

import json

import pytest

from studio.agents import claude_registry
from studio.agents.character_designer import CharacterDesigner
from studio.agents.stubs import FIXTURE_DIR, stub_registry
from studio.coordinator import AgentContext, Coordinator, RunStore
from studio.coordinator.pipeline import load_manifest
from studio.schemas import Characters, Config, StoryBible
from tests.fakes import FakeClient, FakeResponse


@pytest.fixture
def config():
    return Config(story_text=(FIXTURE_DIR / "story.txt").read_text(), target_duration_sec=30, human_approval={"mode": "none"})


@pytest.fixture
def bible():
    return StoryBible.model_validate_json((FIXTURE_DIR / "story_bible.json").read_text())


@pytest.fixture
def characters():
    return Characters.model_validate_json((FIXTURE_DIR / "characters.json").read_text())


def test_prompt_has_bible_manifest_and_policy(config, bible, characters):
    client = FakeClient(FakeResponse(characters))
    manifest = load_manifest(FIXTURE_DIR / "manifest.json")
    ctx = AgentContext(config=config, manifest=manifest, upstream={"story_analyst": bible})
    result = CharacterDesigner(client=client).run(ctx)
    assert isinstance(result, Characters)

    call = client.messages.calls[0]
    assert call["output_format"] is Characters
    user = call["messages"][0]["content"]
    assert "Story bible (from the Story Analyst)" in user and '"char_mia"' in user
    assert "Library manifest" in user and "lib_char_girl_a" in user
    assert '"file"' not in user, "blend file paths are not the agent's business"
    assert "character_mode=parametric" in user and "library_mode=build_requests" in user
    assert "char_mia, char_fox" in user


def test_reference_images_are_passed_through(config, bible, characters):
    config = Config.model_validate({**config.model_dump(), "character_references": [{"character_id_hint": "char_mia", "image_path": "refs/mia.png"}]})
    client = FakeClient(FakeResponse(characters))
    ctx = AgentContext(config=config, manifest=load_manifest(FIXTURE_DIR / "manifest.json"), upstream={"story_analyst": bible})
    CharacterDesigner(client=client).run(ctx)
    user = client.messages.calls[0]["messages"][0]["content"]
    assert "Character reference images" in user and "refs/mia.png" in user


def test_bad_sheet_is_retried_with_check_feedback(tmp_path, config, bible, characters):
    """First answer uses an unknown shape key; the hard checks catch it and the agent
    gets the issue as feedback on the second call."""
    bad = characters.model_copy(deep=True)
    bad.characters[0].expressions["curious"] = {"brow_up": 0.7, "no_such_key": 1.0}
    client = FakeClient(FakeResponse(bad), FakeResponse(characters))
    registry = stub_registry()
    registry.register(CharacterDesigner(client=client))
    store = RunStore(tmp_path / "run")
    Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, store).run()
    assert len(client.messages.calls) == 2
    second = client.messages.calls[1]["messages"][0]["content"]
    assert "Problems with your previous attempt" in second and "no_such_key" in second
    assert store.load_output("character_designer").characters[0].expressions["curious"] == {"brow_up": 0.7}


def test_two_claude_agents_in_one_pipeline(tmp_path, config, bible, characters):
    client = FakeClient(by_schema={StoryBible: FakeResponse(bible), Characters: FakeResponse(characters)})
    registry = stub_registry()
    for agent in claude_registry(client=client)._agents.values():
        registry.register(agent)
    Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, RunStore(tmp_path / "run")).run()
    assert [c["output_format"] for c in client.messages.calls] == [StoryBible, Characters]
    bible_json = json.loads(client.messages.calls[1]["messages"][0]["content"].split("## Story bible")[1].split("## Library")[0].split("\n", 1)[1])
    assert bible_json["title"] == "The Lantern Fox", "the designer saw the analyst's output"
