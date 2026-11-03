"""Director: one style-guide call, one call per scene, partial redo on feedback."""

from __future__ import annotations

import pytest

from studio.agents.director import Director
from studio.agents.stubs import FIXTURE_DIR, stub_registry
from studio.coordinator import AgentContext, Coordinator, RunStore
from studio.coordinator.pipeline import load_manifest
from studio.schemas import Characters, Config, Direction, StoryBible, World
from studio.schemas.direction import SceneDirection, StyleGuide
from tests.fakes import FakeClient, FakeMessages, FakeResponse


@pytest.fixture
def config():
    return Config(story_text=(FIXTURE_DIR / "story.txt").read_text(), target_duration_sec=30, human_approval={"mode": "none"})


@pytest.fixture
def upstream():
    return {
        "story_analyst": StoryBible.model_validate_json((FIXTURE_DIR / "story_bible.json").read_text()),
        "character_designer": Characters.model_validate_json((FIXTURE_DIR / "characters.json").read_text()),
        "scene_builder": World.model_validate_json((FIXTURE_DIR / "world.json").read_text()),
    }


@pytest.fixture
def direction():
    return Direction.model_validate_json((FIXTURE_DIR / "direction.json").read_text())


class SceneAwareMessages(FakeMessages):
    """Answers StyleGuide and SceneDirection requests from a fixture Direction,
    optionally corrupting a scene on the first request for it."""

    def __init__(self, direction: Direction, corrupt_first: dict[str, callable] | None = None):
        super().__init__([])
        self.direction = direction
        self.corrupt_first = dict(corrupt_first or {})
        self.scene_requests: list[str] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        fmt = kwargs["output_format"]
        if fmt is StyleGuide:
            return FakeResponse(self.direction.style_guide)
        assert fmt is SceneDirection
        user = kwargs["messages"][0]["content"]
        sid = user.split("## Scene ")[1].split(" ")[0]
        self.scene_requests.append(sid)
        sd = next(s for s in self.direction.scenes if s.scene_id == sid).model_copy(deep=True)
        if sid in self.corrupt_first:
            self.corrupt_first.pop(sid)(sd)
        return FakeResponse(sd)


def test_style_then_one_call_per_scene(config, upstream, direction):
    client = FakeClient()
    client.messages = SceneAwareMessages(direction)
    ctx = AgentContext(config=config, manifest=load_manifest(FIXTURE_DIR / "manifest.json"), upstream=upstream)
    result = Director(client=client).run(ctx)
    assert isinstance(result, Direction) and [s.scene_id for s in result.scenes] == ["sc_01", "sc_02"]
    assert [c["output_format"] for c in client.messages.calls][0] is StyleGuide
    assert sorted(client.messages.scene_requests) == ["sc_01", "sc_02"]
    style_user = client.messages.calls[0]["messages"][0]["content"]
    assert "## Scenes (in order)" in style_user and "The Lantern Fox" in style_user
    sc1 = next(c for c in client.messages.calls if "## Scene sc_01" in c["messages"][0]["content"])["messages"][0]["content"]
    assert "the film's first" in sc1 and '"Who\'s there?"' in sc1
    assert "anc_path_entry" in sc1 and "## Clip vocabulary" in sc1 and "locomotion" in sc1
    assert "scene_outfit_id" in sc1


def test_only_failing_scene_is_redone(tmp_path, config, upstream, direction):
    """sc_02 first comes back with a prop that does not exist; the hard check flags
    sh_02_01, and only sc_02 is regenerated (style guide and sc_01 kept)."""

    def break_prop(sd: SceneDirection):
        sd.shots[0].prop_ids = ["prop_ghost"]

    client = FakeClient()
    client.messages = SceneAwareMessages(direction, corrupt_first={"sc_02": break_prop})
    config = Config.model_validate({**config.model_dump(), "flags": {"parallel_calls": False}})
    registry = stub_registry()
    registry.register(Director(client=client))
    store = RunStore(tmp_path / "run")
    Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, store).run()

    assert client.messages.scene_requests == ["sc_01", "sc_02", "sc_02"]
    assert sum(1 for c in client.messages.calls if c["output_format"] is StyleGuide) == 1
    retry = client.messages.calls[-1]["messages"][0]["content"]
    assert "Problems with your previous shot list for this scene" in retry and "prop_ghost" in retry
    assert store.load_output("director").scenes[1].shots[0].prop_ids == []


def test_missing_verbatim_line_redoes_its_beat_scene(tmp_path, config, upstream, direction):
    def drop_line(sd: SceneDirection):
        sd.shots[0].dialogue = []

    client = FakeClient()
    client.messages = SceneAwareMessages(direction, corrupt_first={"sc_01": drop_line})
    registry = stub_registry()
    registry.register(Director(client=client))
    Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, RunStore(tmp_path / "run")).run()
    assert sorted(client.messages.scene_requests) == ["sc_01", "sc_01", "sc_02"]
