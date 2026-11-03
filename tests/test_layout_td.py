"""Layout TD: code owns the timeline, Claude drafts the staging per scene."""

from __future__ import annotations

import json

import pytest

from studio.agents.layout_td import LayoutTD, SceneLayoutDraft, ShotLayoutDraft, scene_timeline
from studio.agents.stubs import FIXTURE_DIR, stub_registry
from studio.coordinator import AgentContext, Coordinator, RunStore
from studio.coordinator.pipeline import load_manifest
from studio.schemas import Characters, Config, Direction, Layout, StoryBible, World
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
        "director": Direction.model_validate_json((FIXTURE_DIR / "direction.json").read_text()),
    }


@pytest.fixture
def layout():
    return Layout.model_validate_json((FIXTURE_DIR / "layout.json").read_text())


def draft_from(layout: Layout, scene_id: str, wrong_frames: bool = False) -> SceneLayoutDraft:
    """What Claude would return for a scene: the fixture shots minus the code-owned fields."""
    shots = []
    for sl in layout.shots:
        if sl.scene_id != scene_id:
            continue
        d = sl.model_dump(mode="json", exclude={"scene_id", "frame_start", "frame_end", "environment", "transition_in", "transition_out"})
        for c in d["characters"]:
            c["library_id"] = None  # the agent fills this from the sheets
        if wrong_frames:
            d["camera"]["keyframes"][0]["frame"] = 9999
        shots.append(ShotLayoutDraft.model_validate(d))
    return SceneLayoutDraft(scene_id=scene_id, shots=shots)


class DraftMessages(FakeMessages):
    def __init__(self, layout: Layout, wrong_first: set[str] = frozenset()):
        super().__init__([])
        self.layout = layout
        self.wrong_first = set(wrong_first)
        self.scene_requests: list[str] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["output_format"] is SceneLayoutDraft
        sid = kwargs["messages"][0]["content"].split("## Scene ")[1].split("\n")[0].strip()
        self.scene_requests.append(sid)
        wrong = sid in self.wrong_first
        self.wrong_first.discard(sid)
        return FakeResponse(draft_from(self.layout, sid, wrong_frames=wrong))


def test_timeline_is_contiguous_from_frame_one(upstream):
    d = upstream["director"]
    assert scene_timeline(d.scenes[0], 24) == {"sh_01_01": (1, 240), "sh_01_02": (241, 360)}
    assert scene_timeline(d.scenes[1], 24) == {"sh_02_01": (1, 360)}


def test_code_owned_fields_are_filled(config, upstream, layout):
    client = FakeClient()
    client.messages = DraftMessages(layout)
    ctx = AgentContext(config=config, manifest=load_manifest(FIXTURE_DIR / "manifest.json"), upstream=upstream)
    result = LayoutTD(client=client).run(ctx)
    assert isinstance(result, Layout) and result.fps == 24
    by_id = result.shot_by_id()
    assert (by_id["sh_01_02"].frame_start, by_id["sh_01_02"].frame_end) == (241, 360)
    assert by_id["sh_01_01"].environment.library_id == "lib_env_forest_a" and by_id["sh_01_01"].environment.time_variant == "dusk"
    assert by_id["sh_01_01"].characters[0].library_id == "lib_char_girl_a", "library id comes from the sheet, not the draft"
    assert by_id["sh_01_01"].props[0].library_id == "lib_prop_lantern"
    assert sorted(client.messages.scene_requests) == ["sc_01", "sc_02"]
    user = client.messages.calls[0]["messages"][0]["content"]
    assert "frame ranges are fixed" in user and '"suggested_frame_start"' in user
    assert "clip_walk_cautious" in user and "clip_fox_sit" in user
    assert "## Environment" in user and '"bounds_m"' in user and "anc_stump" in user


def test_full_pipeline_with_layout_td(tmp_path, config, upstream, layout):
    client = FakeClient()
    client.messages = DraftMessages(layout)
    registry = stub_registry()
    registry.register(LayoutTD(client=client))
    store = RunStore(tmp_path / "run")
    Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, store).run()
    assert store.load_output("layout_td").model_dump() == layout.model_dump()
    assert json.loads((store.root / "production_package.json").read_text())["status"] == "ready"


def test_bad_draft_fails_schema_and_only_that_scene_is_redone(tmp_path, config, upstream, layout):
    client = FakeClient()
    client.messages = DraftMessages(layout, wrong_first={"sc_02"})
    config = Config.model_validate({**config.model_dump(), "flags": {"parallel_calls": False}})
    registry = stub_registry()
    registry.register(LayoutTD(client=client))
    store = RunStore(tmp_path / "run")
    Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, store).run()
    # The camera keyframe at 9999 fails ShotLayout validation at assembly; the coordinator
    # feeds the error back and the agent redoes every scene for an unplaceable (schema) error.
    assert client.messages.scene_requests[:2] == ["sc_01", "sc_02"]
    assert client.messages.scene_requests.count("sc_02") == 2
    retry = client.messages.calls[-1]["messages"][0]["content"]
    assert "Problems with your previous layout" in retry and "9999" in retry
