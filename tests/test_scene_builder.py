"""Scene Builder against the fake client and inside the pipeline."""

from __future__ import annotations

import pytest

from studio.agents.scene_builder import SceneBuilder
from studio.agents.stubs import FIXTURE_DIR, stub_registry
from studio.coordinator import AgentContext, Coordinator, RunStore
from studio.coordinator.pipeline import load_manifest
from studio.schemas import Characters, Config, StoryBible, World
from tests.fakes import FakeClient, FakeResponse


@pytest.fixture
def config():
    return Config(story_text=(FIXTURE_DIR / "story.txt").read_text(), target_duration_sec=30, human_approval={"mode": "none"})


@pytest.fixture
def upstream():
    return {
        "story_analyst": StoryBible.model_validate_json((FIXTURE_DIR / "story_bible.json").read_text()),
        "character_designer": Characters.model_validate_json((FIXTURE_DIR / "characters.json").read_text()),
    }


@pytest.fixture
def world():
    return World.model_validate_json((FIXTURE_DIR / "world.json").read_text())


def test_prompt_has_bible_sheets_and_environments(config, upstream, world):
    client = FakeClient(FakeResponse(world))
    ctx = AgentContext(config=config, manifest=load_manifest(FIXTURE_DIR / "manifest.json"), upstream=upstream)
    assert isinstance(SceneBuilder(client=client).run(ctx), World)
    call = client.messages.calls[0]
    assert call["output_format"] is World
    user = call["messages"][0]["content"]
    assert "Character sheets" in user and "out_mia_default" in user and "prop_lantern" in user
    assert "lib_env_forest_a" in user and "path_entry" in user
    assert "design_brief" not in user, "the scene builder gets a trimmed sheet, not the whole character file"
    assert "sum to 27 to 33 seconds" in user and "['loc_forest_clearing']" in user


def test_duration_error_is_fed_back(tmp_path, config, upstream, world):
    long = world.model_copy(deep=True)
    long.scenes[0].estimated_duration_sec = 40.0
    client = FakeClient(FakeResponse(long), FakeResponse(world))
    registry = stub_registry()
    registry.register(SceneBuilder(client=client))
    store = RunStore(tmp_path / "run")
    Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, store).run()
    assert len(client.messages.calls) == 2
    second = client.messages.calls[1]["messages"][0]["content"]
    assert "scenes sum to 55s" in second and "Adjust estimated_duration_sec" in second
    assert store.load_output("scene_builder").scenes[0].estimated_duration_sec == 15.0
