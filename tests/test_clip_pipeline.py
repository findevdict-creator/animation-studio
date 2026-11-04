"""Asset build phase 2: tagging and clip search always; retargeting and procedural layers
when bpy is importable."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from studio.agents.stubs import FIXTURE_DIR
from studio.assets.clips import check_transitions, coverage, find_clip, score_clip
from studio.schemas import Config, Layout, Manifest
from studio.schemas.manifest import LibraryClip
from tools.tag_clips import ALL_TAGS, merge_tags, tags_from_motion, tags_from_name


def test_tags_from_name_and_motion():
    assert tags_from_name("walk_cautious_03") == ["locomotion", "walk", "cautious"]
    assert "run" in tags_from_name("Sprint Forward") and "fast" in tags_from_name("Sprint Forward")
    assert tags_from_name("Standing_Idle_Breath")[:2] == ["idle", "breathe"]
    assert tags_from_name("sit_down_on_chair") == ["transition", "sit_down"]
    assert tags_from_motion((0.0, 0.9, 0.0), 32) == ["locomotion", "slow", "forward"]
    assert tags_from_motion((2.5, 0.2, 0.0), 24) == ["locomotion", "fast", "right"]
    assert tags_from_motion((0.0, 0.0, 0.0), 48) == ["stationary"]
    merged = merge_tags(["walk", "slow"], ["locomotion", "fast", "forward", "not_a_tag"])
    assert merged == ["walk", "slow", "locomotion", "forward"], "one speed, vocabulary only"
    assert set(merged) <= ALL_TAGS


def _clip(id, tags, rig="rigify_human", loop=True, frames=32):
    return LibraryClip(id=id, rig_type=rig, frames=frames, loop=loop, tags=tags)


def test_find_clip_scores_and_procedural_layers():
    manifest = Manifest(
        library_version="t",
        clips=[
            _clip("clip_walk_slow", ["locomotion", "walk", "slow", "forward"]),
            _clip("clip_run", ["locomotion", "run", "fast", "forward"]),
            _clip("clip_wave", ["gesture", "wave"], loop=False, frames=40),
            _clip("clip_fox_sit", ["idle", "sit"], rig="rigify_quadruped"),
        ],
    )
    assert find_clip(manifest, "rigify_human", ["locomotion", "slow"])["clip_id"] == "clip_walk_slow"
    assert find_clip(manifest, "rigify_human", ["locomotion", "fast"])["clip_id"] == "clip_run"
    assert find_clip(manifest, "rigify_human", ["gesture", "wave"])["clip_id"] == "clip_wave"
    assert find_clip(manifest, "rigify_human", ["idle", "breathe"]) == {"source": "procedural", "layer": "breathe", "score": 1.0}
    assert find_clip(manifest, "rigify_human", ["look_at"])["layer"] == "look_at"
    assert find_clip(manifest, "rigify_human", ["dance"]) is None, "a clip gap"
    assert find_clip(manifest, "rigify_quadruped", ["locomotion"]) is None
    baked_only = Config(story_text="x", target_duration_sec=1, flags={"clip_sources": ["baked"]})
    assert find_clip(manifest, "rigify_human", ["idle", "breathe"], baked_only) is None
    assert score_clip(_clip("clip_c", ["locomotion", "fast"]), ["locomotion", "slow"]) < score_clip(_clip("clip_c", ["locomotion", "slow"]), ["locomotion", "slow"])
    cov = coverage(manifest, "rigify_human", [["locomotion", "slow"], ["gesture", "point"], ["idle"]])
    assert cov["covered"] == 2 and cov["gaps"] == [["gesture", "point"]]


def test_transition_table_flags_sit_to_walk():
    manifest = Manifest(library_version="t", clips=[_clip("clip_sit", ["idle", "sit"]), _clip("clip_walk", ["locomotion", "walk"]), _clip("clip_stand_up", ["transition", "stand_up"], loop=False)])
    layout = Layout.model_validate_json((FIXTURE_DIR / "layout.json").read_text())
    layout.shots[0].characters[0].clips = [
        {"clip_id": "clip_sit", "frame_start": 1, "frame_end": 100, "blend_frames": 0},
        {"clip_id": "clip_walk", "frame_start": 101, "frame_end": 240, "blend_frames": 0},
    ]
    layout = Layout.model_validate(layout.model_dump())
    issues = check_transitions(layout, manifest)
    assert len(issues) == 1 and "without a stand_up clip" in issues[0].message and issues[0].owner_agent == "layout_td"
    layout.shots[0].characters[0].clips = [
        {"clip_id": "clip_sit", "frame_start": 1, "frame_end": 100, "blend_frames": 0},
        {"clip_id": "clip_stand_up", "frame_start": 101, "frame_end": 130, "blend_frames": 6},
        {"clip_id": "clip_walk", "frame_start": 131, "frame_end": 240, "blend_frames": 6},
    ]
    assert check_transitions(Layout.model_validate(layout.model_dump()), manifest) == []


def test_clip_tagger_filters_to_vocabulary(tmp_path):
    from studio.agents.clip_tagger import ClipTagger, ClipTags
    from tests.fakes import FakeClient, FakeResponse

    sheet = tmp_path / "sheet.png"
    sheet.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    client = FakeClient(FakeResponse(ClipTags(tags=["gesture", "wave", "happy", "made_up"], confidence=0.8, notes="waves twice")))
    result = ClipTagger(client=client).tag(sheet, "clip_wave_hello", known_tags=["gesture"])
    assert result.tags == ["gesture", "wave", "happy"] and result.confidence == 0.8
    content = client.messages.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert "Vocabulary" in content[1]["text"] and "clip_wave_hello" in content[1]["text"]


def test_retarget_round_trip(tmp_path):
    pytest.importorskip("bpy", reason="bpy wheel not installed; run inside Blender instead")
    from tools import lint_library, retarget, scan_library

    lib = tmp_path / "library"
    shutil.copytree("library", lib)
    source = lib / "clips" / "source" / "mixamo" / "seed_walk_forward.blend"
    bone_map = retarget.load_bone_map(Path("tools/bone_maps/mixamo_to_rigify_human.json"))
    rec = retarget.retarget(lib, source, "base_human_child", bone_map, "clip_walk_forward", licence="CC0")
    assert rec["rig_type"] == "rigify_human" and rec["frames"] == 40 and rec["mapped_bones"] == 11
    # 1.6 m of hip travel on a 1.75 m source scales to the 1.2 m child.
    assert rec["root_motion_m"][1] == pytest.approx(1.6 * 1.176 / 1.75, abs=0.05) and rec["root_motion_m"][0] == pytest.approx(0.0, abs=0.01)
    assert rec["loop"] is True
    assert {"locomotion", "walk", "forward"} <= set(rec["tags"]) and rec["foot_slide_cm"] >= 0
    assert (lib / "clips" / "baked" / "rigify_human" / "clip_walk_forward.blend").exists()

    manifest = Manifest.model_validate(scan_library.scan(lib))
    clip = manifest.clip_by_id()["clip_walk_forward"]
    assert clip.frames == 40 and clip.loop and clip.licence == "CC0" and clip.source == "mixamo/seed_walk_forward.blend"
    assert clip.root_motion_m[1] == pytest.approx(rec["root_motion_m"][1], abs=1e-3)
    assert lint_library.main(["--library", str(lib)]) == 0
    assert find_clip(manifest, "rigify_human", ["locomotion", "forward"])["clip_id"] in ("clip_walk_forward", "clip_walk_cautious")

    # The CLI's exit code reports foot sliding against the threshold.
    args = ["--library", str(lib), "--source", str(source), "--base", "base_human_child", "--map", "tools/bone_maps/mixamo_to_rigify_human.json", "--name", "clip_walk_forward_2", "--licence", "CC0"]
    assert retarget.main(args + ["--max-slide-cm", "1000"]) == 0
    assert retarget.main(args + ["--max-slide-cm", "0"]) == 2


def test_procedural_layers_on_a_base(tmp_path):
    pytest.importorskip("bpy")
    import bpy

    from tools import procedural
    from tools.blend_common import open_blend

    open_blend(Path("library/bases/base_human_child.blend"))
    rig = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    head_mesh = bpy.data.objects["head"]
    assert procedural.add_breathing(rig, "spine", 1, 144) == 8
    blinks = procedural.add_blinks(head_mesh, 1, 240, seed=7)
    assert blinks == procedural.add_blinks(head_mesh, 1, 240, seed=7) and 1 <= len(blinks) <= 6
    target = bpy.data.objects.new("look_target", None)
    bpy.context.scene.collection.objects.link(target)
    con = procedural.add_look_at(rig, "head", target, frame_start=10, frame_end=50)
    assert con is not None and con.type == "DAMPED_TRACK"
    assert len(procedural.add_feet_on_ground(rig, ["foot.R", "foot.L", "no_such"])) == 2
    assert procedural.add_head_follow(rig, "head", "spine").influence == pytest.approx(0.3)
    assert procedural.add_breathing(rig, "no_such_bone", 1, 10) == 0
