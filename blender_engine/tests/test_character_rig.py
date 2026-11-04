"""Rig standard on the stand-in template: Rigify generation, weights, face drivers, eye
tracking, and the full validator, on a freshly built character and on the same character
saved and reopened with scripts disabled (as every studio job opens files)."""

import pytest

bpy = pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")

from cartoon_engine.character.build import build_geometry  # noqa: E402
from cartoon_engine.character.rig import build_rig  # noqa: E402
from cartoon_engine.character.validate import find_parts, validate  # noqa: E402
from cartoon_engine.core import blend_io  # noqa: E402
from studio_contracts.character import CharacterSpecification, resolve_spec  # noqa: E402


def build(**over):
    rc = resolve_spec(CharacterSpecification.model_validate({"name": "Rig", "body": {"age_appearance": "child"},
                                                             **over}))
    blend_io.reset_empty()
    ch = build_geometry(rc)
    stats = build_rig(ch)
    return rc, ch, stats


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    rc, ch, stats = build()
    path = tmp_path_factory.mktemp("rig") / "character.blend"
    blend_io.save(path)
    return rc, path, stats


def test_fresh_rig_validates():
    rc, ch, stats = build(hair={"style": "spiky", "color": "red"})
    report = validate(find_parts(ch.collection), rc.height_m)
    assert report.ok, report.failed()
    assert stats["drivers"] == 43 and stats["bones"] > 300


def test_reopened_file_validates_without_scripts(saved):
    rc, path, _ = saved
    blend_io.open_file(path)  # use_scripts=False: Rigify's rig_ui.py does not run
    report = validate(find_parts(), rc.height_m)
    assert report.ok, [c for c in report.checks if not c["ok"]]
    names = {c["name"] for c in report.checks}
    assert {"fk_arm_moves_hand_only", "ik_leg_lifts_foot", "head_turn", "face_controls_move_face",
            "visemes_override_mouth", "eyes_track_target"} <= names


def test_validator_catches_a_broken_rig(saved):
    rc, path, _ = saved
    blend_io.open_file(path)
    parts = find_parts()
    parts.head.data.shape_keys.key_blocks["vis_O"].name = "vis_Oh"
    body = parts.body
    body.vertex_groups.remove(body.vertex_groups["DEF-hand.L"])
    parts.hair.scale = (1.1, 1.1, 1.1)
    report = validate(parts, rc.height_m)
    failed = set(report.failed())
    assert {"shape_keys", "shape_key_drivers", "skin_weights", "applied_transforms"} <= failed


@pytest.mark.parametrize("age", ["teen", "adult", "elder"])
def test_every_age_rigs(age):
    rc, ch, _ = build(body={"age_appearance": age, "body_type": "stocky"})
    report = validate(find_parts(ch.collection), rc.height_m)
    assert report.ok, report.failed()
