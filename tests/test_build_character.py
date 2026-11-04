"""Asset build phase 1: pure helpers always; the Blender build when bpy is importable."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from studio.agents.stubs import FIXTURE_DIR
from studio.assets.characters import library_id_for, plan_character_builds, promote_built
from studio.schemas import Characters, Manifest
from tools.build_character import hex_to_rgba, parametric_entries, slider_value

PARAMETRIC = FIXTURE_DIR / "characters_parametric.json"


def test_pure_helpers():
    assert library_id_for("char_mia") == "lib_char_mia" and library_id_for("mia") == "lib_char_mia"
    assert slider_value(1.0) == 0.0 and slider_value(1.15) == pytest.approx(0.5) and slider_value(0.5) == -1.0
    r, g, b, a = hex_to_rgba("#FF8000")
    assert a == 1.0 and r == 1.0 and 0.2 < g < 0.25 and b == 0.0


def test_plan_and_promote():
    chars = Characters.model_validate_json(PARAMETRIC.read_text())
    manifest = Manifest.model_validate_json(Path("library/manifest.json").read_text())
    plan = plan_character_builds(chars, manifest)
    assert [p["character_id"] for p in plan] == ["char_mia", "char_fox"]
    mia = plan[0]
    assert mia["base_exists"] and not mia["already_built"] and mia["garments"] == ["garm_boots_a", "garm_raincoat_a"]
    assert mia["hair_id"] == "hair_two_braids"

    promoted = promote_built(chars, [{"character_id": "char_mia", "library_id": "lib_char_mia"}])
    by = promoted.by_id()
    assert by["char_mia"].asset.status == "library" and by["char_mia"].asset.library_id == "lib_char_mia"
    assert by["char_mia"].asset.base_id == "base_human_child", "parametric fields stay for rebuilds"
    assert by["char_fox"].asset.status == "parametric"
    assert len(parametric_entries(json.loads(PARAMETRIC.read_text()))) == 2


def test_build_from_bases_round_trip(tmp_path):
    pytest.importorskip("bpy", reason="bpy wheel not installed; run inside Blender instead")
    from studio import checks
    from studio.schemas import Config, StoryBible
    from tools import build_character, lint_library, scan_library

    lib = tmp_path / "library"
    shutil.copytree("library", lib)
    characters = json.loads(PARAMETRIC.read_text())
    records = build_character.build_all(lib, characters)
    assert [r["library_id"] for r in records] == ["lib_char_mia", "lib_char_fox"]
    mia = records[0]
    assert mia["sliders"] == ["age", "head_scale", "limb_length"] and mia["bone_overrides"] == ["upper_arm.R", "upper_arm.L"]
    assert mia["garments"] == ["garm_raincoat_a", "garm_boots_a"] and mia["hair_id"] == "hair_two_braids"
    assert mia["height_m"] == pytest.approx(1.2, abs=0.01) and mia["warnings"] == []
    assert (lib / "characters" / "lib_char_mia.blend").exists()

    manifest = Manifest.model_validate(scan_library.scan(lib))
    built = manifest.character_by_id()["lib_char_mia"]
    assert built.rig_type == "rigify_human" and "coat" in built.outfit_slots
    assert "smile" in built.shape_keys and "head_scale" not in built.shape_keys, "sliders are not expressions"
    assert "protagonist" in built.tags and "base_human_child" in built.tags
    assert lint_library.main(["--library", str(lib)]) == 0

    # The promoted sheets pass the stage 1 hard checks against the rescanned manifest.
    promoted = promote_built(Characters.model_validate(characters), records)
    bible = StoryBible.model_validate_json((FIXTURE_DIR / "story_bible.json").read_text())
    config = Config(story_text="x", target_duration_sec=30)
    assert [i for i in checks.check_characters(bible, promoted, manifest, config) if i.severity == "error"] == []


def test_cli_build_characters(tmp_path):
    pytest.importorskip("bpy")
    from studio.cli import main
    from studio.coordinator import RunStore

    lib = tmp_path / "library"
    shutil.copytree("library", lib)
    store = RunStore(tmp_path / "run")
    store.save_output("character_designer", Characters.model_validate_json(PARAMETRIC.read_text()))
    assert main(["build-characters", str(tmp_path / "run"), "--library", str(lib), "--only", "char_mia"]) == 0
    assert store.load_output("character_designer").by_id()["char_mia"].asset.library_id == "lib_char_mia"
    assert store.load_output("character_designer").by_id()["char_fox"].asset.status == "parametric"
    report = json.loads((tmp_path / "run" / "assets" / "character_builds.json").read_text())
    assert [r["library_id"] for r in report] == ["lib_char_mia"]
    assert "lib_char_mia" in {c["id"] for c in json.loads((lib / "manifest.json").read_text())["characters"]}
