"""Pure-function tests always run; Blender round trips run only when ``bpy`` is importable."""

from pathlib import Path

import pytest

from tools.blend_common import kind_from_path, parse_params, script_args, split_csv


def test_kind_from_path(tmp_path):
    lib = tmp_path
    assert kind_from_path(lib, lib / "characters" / "lib_char_a.blend") == "character"
    assert kind_from_path(lib, lib / "clips" / "baked" / "rigify_human" / "clip_x.blend") == "clip"
    assert kind_from_path(lib, lib / "kits" / "forest" / "kit_forest_stump.blend") == "kit_piece"
    assert kind_from_path(lib, lib / "random" / "x.blend") is None


def test_script_args_strip_blender_prefix():
    assert script_args(["-b", "--python", "x.py", "--", "--library", "lib"]) == ["--library", "lib"]
    assert script_args(["--library", "lib"]) == ["--library", "lib"]


def test_split_and_params():
    assert split_csv(" a, b ,,c") == ["a", "b", "c"]
    assert split_csv(None) == []
    assert parse_params("height_m:0:10,roughness:0:1") == {"height_m": (0.0, 10.0), "roughness": (0.0, 1.0)}


def test_seed_scan_lint_round_trip(tmp_path):
    pytest.importorskip("bpy", reason="bpy wheel not installed; run inside Blender instead")
    from tools import lint_library, make_seed_library, scan_library
    from studio.schemas import Manifest

    lib = tmp_path / "library"
    make_seed_library.build(lib)
    assert (lib / "characters" / "lib_char_girl_a.blend").exists()

    manifest = Manifest.model_validate(scan_library.scan(lib))
    assert {c.id for c in manifest.characters} == {"lib_char_girl_a", "lib_char_fox_a"}
    girl = manifest.character_by_id()["lib_char_girl_a"]
    assert girl.rig_type == "rigify_human" and abs(girl.height_m - 1.2) < 0.05
    assert "smile" in girl.shape_keys and "coat" in girl.outfit_slots
    assert manifest.prop_by_id()["lib_prop_lantern"].attach_bone == "hand.R"
    forest = manifest.environment_by_id()["lib_env_forest_a"]
    assert set(forest.anchors) >= {"center", "stump", "path_entry"}
    assert forest.time_variants == ["day", "dusk", "night"]
    walk = manifest.clip_by_id()["clip_walk_cautious"]
    assert walk.frames == 32 and walk.loop and abs(walk.root_motion_m[1] - 1.1) < 1e-3
    assert manifest.clip_by_id()["clip_raise_arm_r"].loop is False
    base = manifest.base_by_id()["base_human_child"]
    assert base.proportion_sliders == ["head_scale", "limb_length"]
    assert "head_scale" not in base.shape_keys
    assert manifest.lighting_presets == ["day", "dusk", "night", "interior_warm"]

    assert lint_library.main(["--library", str(lib)]) == 0

    out = tmp_path / "manifest.json"
    assert scan_library.main(["--library", str(lib), "--out", str(out)]) == 0
    assert Manifest.model_validate_json(Path(out).read_text()).library_version
