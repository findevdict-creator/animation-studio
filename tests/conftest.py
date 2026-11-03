"""A small but complete fixture story: one girl, one fox, one clearing, two scenes."""

from __future__ import annotations

import pytest

from studio.schemas import Characters, Config, Direction, Layout, Manifest, StoryBible, World


@pytest.fixture
def config() -> Config:
    return Config(story_text="Mia finds a glowing fox at the forest edge.", target_duration_sec=30)


@pytest.fixture
def manifest() -> Manifest:
    return Manifest.model_validate(
        {
            "library_version": "test",
            "characters": [
                {
                    "id": "lib_char_girl_a",
                    "file": "chars/girl_a.blend",
                    "rig_type": "rigify_human",
                    "height_m": 1.2,
                    "outfit_slots": ["top", "bottom", "shoes", "coat"],
                    "shape_keys": ["smile", "frown", "brow_up", "mouth_open"],
                },
                {
                    "id": "lib_char_fox_a",
                    "file": "chars/fox_a.blend",
                    "rig_type": "rigify_quadruped",
                    "height_m": 0.5,
                    "shape_keys": ["smile", "ears_back"],
                },
            ],
            "props": [
                {"id": "lib_prop_lantern", "file": "props/lantern.blend", "bounds_m": [0.2, 0.2, 0.35], "attach_bone": "hand.R"}
            ],
            "environments": [
                {
                    "id": "lib_env_forest_a",
                    "file": "envs/forest_a.blend",
                    "bounds_m": [60, 60, 20],
                    "anchors": ["center", "stump", "path_entry"],
                    "time_variants": ["day", "dusk", "night"],
                }
            ],
            "clips": [
                {"id": "clip_walk_cautious", "rig_type": "rigify_human", "frames": 32, "loop": True, "root_motion_m": [0, 1.1, 0], "tags": ["locomotion", "slow"]},
                {"id": "clip_raise_arm_r", "rig_type": "rigify_human", "frames": 18, "loop": False, "tags": ["gesture"]},
                {"id": "clip_fox_sit", "rig_type": "rigify_quadruped", "frames": 24, "loop": True, "tags": ["idle"]},
            ],
            "bases": [
                {
                    "id": "base_human_child",
                    "rig_type": "rigify_human",
                    "proportion_sliders": ["head_scale", "limb_length"],
                    "material_slots": ["skin", "hair", "eyes"],
                    "outfit_slots": ["top", "bottom", "shoes", "coat"],
                    "shape_keys": ["smile", "frown", "brow_up", "mouth_open"],
                }
            ],
        }
    )


@pytest.fixture
def bible() -> StoryBible:
    return StoryBible.model_validate(
        {
            "title": "The Lantern Fox",
            "logline": "A girl follows a glowing fox into the woods.",
            "synopsis": "Mia sees a glowing fox at dusk and follows it to a clearing.",
            "cast_index": [
                {"id": "char_mia", "name": "Mia", "role": "protagonist"},
                {"id": "char_fox", "name": "The fox", "aliases": ["the glow"], "role": "supporting"},
            ],
            "location_index": [{"id": "loc_forest_clearing", "name": "Forest clearing"}],
            "beats": [
                {
                    "id": "beat_001",
                    "order": 1,
                    "summary": "Mia sees the fox.",
                    "story_function": "setup",
                    "location_id": "loc_forest_clearing",
                    "time_of_day": "dusk",
                    "character_ids": ["char_mia", "char_fox"],
                    "dialogue": [{"speaker_id": "char_mia", "text": "Who's there?", "source": "verbatim"}],
                },
                {
                    "id": "beat_002",
                    "order": 2,
                    "summary": "The fox sits and waits.",
                    "story_function": "inciting_incident",
                    "location_id": "loc_forest_clearing",
                    "time_of_day": "dusk",
                    "character_ids": ["char_mia", "char_fox"],
                },
            ],
        }
    )


@pytest.fixture
def characters() -> Characters:
    return Characters.model_validate(
        {
            "characters": [
                {
                    "id": "char_mia",
                    "name": "Mia",
                    "role": "protagonist",
                    "default_outfit_id": "out_mia_default",
                    "outfits": [
                        {"id": "out_mia_default", "description": "red raincoat", "slots": {"coat": {"base_color": "#C0392B"}}}
                    ],
                    "expressions": {"neutral": {}, "curious": {"brow_up": 0.7}},
                    "asset": {
                        "status": "library",
                        "library_id": "lib_char_girl_a",
                        "rig_type": "rigify_human",
                        "height_m": 1.2,
                    },
                    "signature_prop_ids": ["prop_lantern"],
                },
                {
                    "id": "char_fox",
                    "name": "The fox",
                    "role": "supporting",
                    "species": "fox",
                    "default_outfit_id": "out_fox_default",
                    "outfits": [{"id": "out_fox_default", "slots": {}}],
                    "expressions": {"neutral": {}, "alert": {"ears_back": 0.5}},
                    "asset": {
                        "status": "library",
                        "library_id": "lib_char_fox_a",
                        "rig_type": "rigify_quadruped",
                        "height_m": 0.5,
                    },
                },
            ]
        }
    )


@pytest.fixture
def world() -> World:
    return World.model_validate(
        {
            "locations": [
                {
                    "id": "loc_forest_clearing",
                    "name": "Forest clearing",
                    "setting_type": "exterior",
                    "asset": {"status": "library", "library_id": "lib_env_forest_a", "time_variant": "dusk"},
                    "anchors": [
                        {"id": "anc_stump", "library_anchor": "stump", "position_m": [0, 0, 0]},
                        {"id": "anc_path_entry", "library_anchor": "path_entry", "position_m": [-8, -12, 0], "facing_deg": 35},
                        {"id": "anc_fox_spot", "position_m": [6, 4, 0], "facing_deg": 200},
                    ],
                }
            ],
            "props": [
                {
                    "id": "prop_lantern",
                    "name": "Brass lantern",
                    "owner_id": "char_mia",
                    "asset": {"status": "library", "library_id": "lib_prop_lantern"},
                    "emits_light": {"color": "#FFB347", "energy_w": 40},
                }
            ],
            "scenes": [
                {
                    "id": "sc_01",
                    "order": 1,
                    "title": "The glow",
                    "beat_ids": ["beat_001"],
                    "location_id": "loc_forest_clearing",
                    "time_of_day": "dusk",
                    "cast": [
                        {"character_id": "char_mia", "outfit_id": "out_mia_default", "start_anchor_id": "anc_path_entry"},
                        {"character_id": "char_fox", "outfit_id": "out_fox_default", "start_anchor_id": "anc_fox_spot"},
                    ],
                    "prop_ids": ["prop_lantern"],
                    "estimated_duration_sec": 15,
                },
                {
                    "id": "sc_02",
                    "order": 2,
                    "title": "The wait",
                    "beat_ids": ["beat_002"],
                    "location_id": "loc_forest_clearing",
                    "time_of_day": "dusk",
                    "cast": [
                        {"character_id": "char_mia", "outfit_id": "out_mia_default"},
                        {"character_id": "char_fox", "outfit_id": "out_fox_default"},
                    ],
                    "estimated_duration_sec": 15,
                },
            ],
        }
    )


@pytest.fixture
def direction() -> Direction:
    return Direction.model_validate(
        {
            "style_guide": {"visual_style": "toon"},
            "scenes": [
                {
                    "scene_id": "sc_01",
                    "duration_sec": 15,
                    "shots": [
                        {
                            "id": "sh_01_01",
                            "order": 1,
                            "beat_id": "beat_001",
                            "duration_sec": 10,
                            "shot_type": "LS",
                            "subject_id": "char_mia",
                            "characters": [
                                {
                                    "character_id": "char_mia",
                                    "outfit_id": "out_mia_default",
                                    "from_anchor_id": "anc_path_entry",
                                    "to_anchor_id": "anc_stump",
                                    "expression": "curious",
                                    "action": "Walks to the stump.",
                                    "action_tags": ["locomotion", "slow"],
                                },
                                {"character_id": "char_fox", "outfit_id": "out_fox_default", "expression": "alert"},
                            ],
                            "prop_ids": ["prop_lantern"],
                            "dialogue": [
                                {"line_id": "ln_01_01_01", "speaker_id": "char_mia", "text": "Who's there?", "start_sec": 6}
                            ],
                        },
                        {
                            "id": "sh_01_02",
                            "order": 2,
                            "beat_id": "beat_001",
                            "duration_sec": 5,
                            "shot_type": "CU",
                            "characters": [{"character_id": "char_fox", "outfit_id": "out_fox_default", "expression": "alert"}],
                        },
                    ],
                },
                {
                    "scene_id": "sc_02",
                    "duration_sec": 15,
                    "shots": [
                        {
                            "id": "sh_02_01",
                            "order": 1,
                            "beat_id": "beat_002",
                            "duration_sec": 15,
                            "shot_type": "MS",
                            "characters": [
                                {"character_id": "char_mia", "outfit_id": "out_mia_default"},
                                {"character_id": "char_fox", "outfit_id": "out_fox_default"},
                            ],
                        }
                    ],
                },
            ],
        }
    )


def _shot_layout(shot_id, scene_id, start, end, chars, cam_pos, look_at, dialogue=()):
    return {
        "shot_id": shot_id,
        "scene_id": scene_id,
        "frame_start": start,
        "frame_end": end,
        "environment": {"library_id": "lib_env_forest_a", "time_variant": "dusk"},
        "camera": {
            "rig": "static",
            "keyframes": [{"frame": start, "position_m": cam_pos, "look_at_m": look_at, "focal_mm": 35}],
        },
        "characters": chars,
        "props": [{"prop_id": "prop_lantern", "library_id": "lib_prop_lantern", "placement": "attached"}],
        "lights": [{"id": "key", "type": "sun", "rotation_deg": [45, 0, 30], "color": "#BFD4FF", "energy": 3}],
        "dialogue_timing": list(dialogue),
    }


@pytest.fixture
def layout() -> Layout:
    mia_walk = {
        "character_id": "char_mia",
        "library_id": "lib_char_girl_a",
        "outfit_id": "out_mia_default",
        "path": [
            {"frame": 1, "position_m": [-8, -12, 0], "facing_deg": 35},
            {"frame": 200, "position_m": [-0.6, -0.8, 0], "facing_deg": 10},
        ],
        "clips": [
            {"clip_id": "clip_walk_cautious", "frame_start": 1, "frame_end": 200, "blend_frames": 6},
            {"clip_id": "clip_raise_arm_r", "frame_start": 201, "frame_end": 218, "blend_frames": 6},
        ],
        "expression_keys": [{"frame": 1, "expression": "neutral"}, {"frame": 150, "expression": "curious"}],
        "attachments": [{"prop_id": "prop_lantern", "bone": "hand.R", "from_frame": 1}],
    }
    fox = {
        "character_id": "char_fox",
        "library_id": "lib_char_fox_a",
        "outfit_id": "out_fox_default",
        "path": [{"frame": 1, "position_m": [6, 4, 0], "facing_deg": 200}],
        "clips": [{"clip_id": "clip_fox_sit", "frame_start": 1, "frame_end": 240}],
    }
    fox_cu = dict(fox, clips=[{"clip_id": "clip_fox_sit", "frame_start": 241, "frame_end": 360}], path=[{"frame": 241, "position_m": [6, 4, 0]}])
    mia_still = dict(mia_walk, path=[{"frame": 1, "position_m": [-0.6, -0.8, 0]}], clips=[{"clip_id": "clip_walk_cautious", "frame_start": 1, "frame_end": 360}], expression_keys=[])
    fox_still = dict(fox, clips=[{"clip_id": "clip_fox_sit", "frame_start": 1, "frame_end": 360}])
    return Layout.model_validate(
        {
            "fps": 24,
            "shots": [
                _shot_layout(
                    "sh_01_01", "sc_01", 1, 240, [mia_walk, fox], [-4, -30, 6], [0, 0, 0.8],
                    dialogue=[{"line_id": "ln_01_01_01", "speaker_id": "char_mia", "frame_start": 145, "frame_end": 175}],
                ),
                _shot_layout("sh_01_02", "sc_01", 241, 360, [fox_cu], [6, 1, 0.5], [6, 4, 0.3]),
                _shot_layout("sh_02_01", "sc_02", 1, 360, [mia_still, fox_still], [3, -10, 1.5], [2, 0, 0.6]),
            ],
        }
    )
