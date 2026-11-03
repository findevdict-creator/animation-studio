import pytest
from pydantic import ValidationError

from studio.ids import kind_of, line_shot, shot_scene
from studio.schemas import AGENT_OUTPUTS, Characters, Direction, Flags, HumanApproval, Layout, StoryBible


def test_id_kinds():
    assert kind_of("char_mia") == "character"
    assert kind_of("beat_001") == "beat"
    assert kind_of("sh_01_02") == "shot"
    assert kind_of("br_prop_lantern") == "build_request"
    assert kind_of("Mia") is None
    assert shot_scene("sh_03_07") == "sc_03"
    assert line_shot("ln_03_07_02") == "sh_03_07"


def test_fixtures_validate(bible, characters, world, direction, layout, manifest):
    for model in (bible, characters, world, direction, layout, manifest):
        model.model_validate(model.model_dump())


def test_round_trip_json(bible):
    again = StoryBible.model_validate_json(bible.model_dump_json())
    assert again == bible


def test_unknown_field_rejected(bible):
    data = bible.model_dump()
    data["mood"] = "warm"
    with pytest.raises(ValidationError):
        StoryBible.model_validate(data)


def test_bible_rejects_unknown_speaker(bible):
    data = bible.model_dump()
    data["beats"][0]["dialogue"][0]["speaker_id"] = "char_ghost"
    with pytest.raises(ValidationError, match="unknown speaker"):
        StoryBible.model_validate(data)


def test_character_default_outfit_must_exist(characters):
    data = characters.model_dump()
    data["characters"][0]["default_outfit_id"] = "out_mia_other"
    with pytest.raises(ValidationError, match="default_outfit_id"):
        Characters.model_validate(data)


def test_asset_status_needs_matching_field(characters):
    data = characters.model_dump()
    data["characters"][0]["asset"] = {"status": "build_request", "rig_type": "rigify_human", "height_m": 1.2}
    with pytest.raises(ValidationError, match="build_request_id"):
        Characters.model_validate(data)


def test_shot_durations_must_sum(direction):
    data = direction.model_dump()
    data["scenes"][0]["duration_sec"] = 40
    with pytest.raises(ValidationError, match="shots sum"):
        Direction.model_validate(data)


def test_line_must_belong_to_shot(direction):
    data = direction.model_dump()
    data["scenes"][0]["shots"][0]["dialogue"][0]["line_id"] = "ln_02_01_01"
    with pytest.raises(ValidationError, match="another shot"):
        Direction.model_validate(data)


def test_layout_clip_overlap_rejected(layout):
    data = layout.model_dump()
    clips = data["shots"][0]["characters"][0]["clips"]
    clips[1]["frame_start"] = 150
    with pytest.raises(ValidationError, match="overlap"):
        Layout.model_validate(data)


def test_layout_keyframe_outside_shot_rejected(layout):
    data = layout.model_dump()
    data["shots"][0]["camera"]["keyframes"][0]["frame"] = 999
    with pytest.raises(ValidationError, match="outside"):
        Layout.model_validate(data)


def test_flags_consistency():
    with pytest.raises(ValidationError):
        Flags(render_backend="blender", layout_td=False)
    assert Flags(render_backend="none", layout_td=False).layout_td is False


def test_human_approval_modes():
    assert HumanApproval().gate_on("director")
    assert not HumanApproval(mode="none").gate_on("director")
    sel = HumanApproval(mode="selected", agents=["story_analyst"])
    assert sel.gate_on("story_analyst") and not sel.gate_on("director")


def test_every_agent_has_json_schema():
    for agent, model in AGENT_OUTPUTS.items():
        schema = model.model_json_schema()
        assert schema["type"] == "object", agent
