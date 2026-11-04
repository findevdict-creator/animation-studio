"""Asset build phase 3: pure helpers always; the Blender composition when bpy is importable."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from studio.agents.stubs import FIXTURE_DIR
from studio.assets.environments import library_id_for, plan_environment_builds, promote_built
from studio.schemas import Manifest, World
from tools.compose_env import scatter_points, terrain_height

COMPOSED = FIXTURE_DIR / "world_composed.json"


def test_scatter_is_deterministic_and_respects_exclusions():
    a = scatter_points(4021, 0, (60, 60, 20), 0.4, 1.5, [(0, 0.9), (6, 4.5)])
    b = scatter_points(4021, 0, (60, 60, 20), 0.4, 1.5, [(0, 0.9), (6, 4.5)])
    assert a == b and len(a) == round(0.4 * 6 * 36)
    assert all((x - 0) ** 2 + (y - 0.9) ** 2 >= 1.5**2 for x, y, _, _ in a)
    assert scatter_points(4022, 0, (60, 60, 20), 0.4, 1.5, []) != a, "another seed, another set"
    ring = scatter_points(1, 1, (60, 60, 20), 0.15, 0.0, [], ring={"inner_m": 9, "outer_m": 28})
    assert ring and all(9**2 <= x * x + y * y <= 28**2 for x, y, _, _ in ring)
    assert scatter_points(1, 0, (10, 10, 5), 0.0, 0.0, []) == []


def test_terrain_height_is_zero_at_origin_and_repeatable():
    assert terrain_height(0, 0, 4021, 1.5, 0.3) == pytest.approx(0.0, abs=1e-6)
    assert terrain_height(12.5, -7.0, 4021, 1.5, 0.3) == terrain_height(12.5, -7.0, 4021, 1.5, 0.3)
    assert terrain_height(12.5, -7.0, 4021, 0.0, 0.3) == 0.0
    assert library_id_for("loc_forest_clearing") == "lib_env_forest_clearing"


def test_plan_and_promote():
    world = World.model_validate_json(COMPOSED.read_text())
    manifest = Manifest.model_validate_json(Path("library/manifest.json").read_text())
    plan = plan_environment_builds(world, manifest)
    assert len(plan) == 1 and plan[0]["kit_exists"] and plan[0]["missing_pieces"] == [] and plan[0]["missing_generators"] == []
    assert plan[0]["library_id"] == "lib_env_forest_clearing" and not plan[0]["already_built"]
    promoted = promote_built(world, [{"location_id": "loc_forest_clearing", "library_id": "lib_env_forest_clearing", "time_variants": ["day", "dusk", "night"]}])
    loc = promoted.location_by_id()["loc_forest_clearing"]
    assert loc.asset.status == "library" and loc.asset.library_id == "lib_env_forest_clearing" and loc.asset.time_variant == "dusk"
    assert loc.asset.kit_id == "kit_forest", "the composition stays for rebuilds"


def test_compose_round_trip(tmp_path):
    pytest.importorskip("bpy", reason="bpy wheel not installed; run inside Blender instead")
    from studio import checks
    from studio.schemas import Characters, Config, StoryBible
    from tools import compose_env, lint_library, scan_library

    lib = tmp_path / "library"
    shutil.copytree("library", lib)
    world = json.loads(COMPOSED.read_text())
    records = compose_env.compose_all(lib, world)
    rec = records[0]
    assert rec["library_id"] == "lib_env_forest_clearing" and rec["pieces"] == 2 and rec["warnings"] == []
    assert rec["scatter"]["gen_scatter_trees"] > 0 and rec["scatter"]["gen_scatter_grass"] > 0
    assert rec["time_variants"] == ["day", "dusk", "night"]

    manifest = Manifest.model_validate(scan_library.scan(lib))
    env = manifest.environment_by_id()["lib_env_forest_clearing"]
    assert set(env.anchors) == {"stump", "path_entry", "fox_spot"} and env.time_variants == ["day", "dusk", "night"]
    assert env.bounds_m[0] == pytest.approx(60.0, abs=0.5)
    assert lint_library.main(["--library", str(lib)]) == 0

    # Same composition twice gives the same set.
    again = compose_env.compose_environment(lib, world["locations"][0], out_dir=tmp_path / "again")
    assert again["bounds_m"] == rec["bounds_m"] and again["scatter"] == rec["scatter"]

    promoted = promote_built(World.model_validate(world), records)
    bible = StoryBible.model_validate_json((FIXTURE_DIR / "story_bible.json").read_text())
    chars = Characters.model_validate_json((FIXTURE_DIR / "characters.json").read_text())
    issues = checks.check_world(bible, chars, promoted, manifest, Config(story_text="x", target_duration_sec=30))
    assert [i for i in issues if i.severity == "error"] == []


def test_cli_build_environments(tmp_path):
    pytest.importorskip("bpy")
    from studio.cli import main
    from studio.coordinator import RunStore

    lib = tmp_path / "library"
    shutil.copytree("library", lib)
    store = RunStore(tmp_path / "run")
    store.save_output("scene_builder", World.model_validate_json(COMPOSED.read_text()))
    assert main(["build-environments", str(tmp_path / "run"), "--library", str(lib)]) == 0
    loc = store.load_output("scene_builder").location_by_id()["loc_forest_clearing"]
    assert loc.asset.status == "library" and loc.asset.library_id == "lib_env_forest_clearing"
    assert (tmp_path / "run" / "assets" / "environment_builds.json").exists()
