"""The animation system in Blender: every generator baked and played on two bodies, the CMU
walk retargeted, the two jobs as the worker runs them, and a library animation in a scene."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")

_spec = importlib.util.spec_from_file_location("outfit_helpers", Path(__file__).with_name("test_outfit_fit.py"))
_h = sys.modules.setdefault("outfit_helpers", importlib.util.module_from_spec(_spec))
_spec.loader.exec_module(_h)
run_job, sha = _h.run_job, _h.sha

from cartoon_engine.animation import generators as G
from cartoon_engine.core import blend_io
from cartoon_engine.jobs import animation_common as AC
from studio_contracts.animation import (
    GENERATORS,
    AnimationCompat,
    AnimationSpecification,
    resolve_animation,
)

ROOT = Path(__file__).resolve().parents[2]
CMU_WALK = ROOT / "samples" / "mocap" / "cmu_02_01_walk.bvh"
FAST = {"tile": 64, "samples": 1}


def gen_spec(key: str, **kw) -> AnimationSpecification:
    g = next(g for g in GENERATORS if g.key == key)
    src = {"kind": "generator", "generator": {"id": g.id, "version": g.version}, **kw}
    return AnimationSpecification(name=f"test {key}", source=src)


def bvh_spec() -> AnimationSpecification:
    return AnimationSpecification(name="CMU walk", category="locomotion", source={
        "kind": "bvh", "upload_key": "uploads/00000000-0000-7000-8000-000000000000/cmu_02_01_walk.bvh",
        "sha256": sha(CMU_WALK), "skeleton": "cmu", "travels": True, "licence": "CMU mocap"})


@pytest.fixture(scope="module")
def baked(tmp_path_factory):
    """Every generator baked on the reference body and saved, with its compat record."""
    d = tmp_path_factory.mktemp("anims")
    out = {}
    for g in GENERATORS:
        spec = gen_spec(g.key, **({"expression": "surprised"} if g.needs_expression else {}))
        ra = resolve_animation(spec)
        blend_io.reset_empty()
        _, parts = AC.reference_body(ra.reference_fit_profile, ra.style_profile)
        action, build, speed = AC.make_action(ra, parts.rig, d)
        compat = AC.describe(ra, parts.rig, action, build["frames"], speed)
        AC.stamp(action, ra, compat, ra.digest())
        path = d / f"{g.key}.blend"
        AC.save_action(action, path)
        out[g.key] = (ra, compat, path)
    return out


@pytest.mark.parametrize("key", [g.key for g in GENERATORS])
def test_generator_plays_on_two_bodies(baked, key):
    ra, compat, path = baked[key]
    assert not [c for c in compat.controls if c.startswith(("DEF-", "ORG-", "MCH-"))]
    plays = [AC.play_and_measure(path, ra, compat, fp) for fp in ("child_average", "adult_average")]
    report = AC.judge(ra, compat, plays, True)
    assert report["ok"], report["failed"]
    if key == "walk":
        assert compat.travels and compat.stride_m > 0.3
        child, adult = plays
        # the adult's location keys are scaled up: a longer stride on longer legs
        assert adult["leg_ratio"] > 1.3 and adult["travel_m"] > child["travel_m"] * 1.3
        assert max(child["max_slide_cm"], adult["max_slide_cm"]) <= 1.0
    if key == "expression":
        assert compat.face_props == ("expr_surprised",)
        assert plays[0]["face_peaks"]["expr_surprised"] == pytest.approx(1.0, abs=0.01)


def test_cmu_walk_is_retargeted(tmp_path):
    ra = resolve_animation(bvh_spec())
    (tmp_path / "inputs").mkdir()
    shutil.copy(CMU_WALK, tmp_path / "inputs" / "source.bvh")
    blend_io.reset_empty()
    _, parts = AC.reference_body("child_average", ra.style_profile)
    action, info, speed = AC.make_action(ra, parts.rig, tmp_path)
    assert info["source_fps"] == 120.0 and info["frames"] > 60
    assert set(info["mapped"].values()) >= {"torso", "thigh_fk.L", "shin_fk.R", "upper_arm_fk.L", "head"}
    compat = AC.describe(ra, parts.rig, action, info["frames"], speed)
    assert compat.travels and 1.0 < compat.stride_m < 2.0  # about 1.4 m of forward travel, child-sized
    AC.stamp(action, ra, compat, ra.digest())
    AC.save_action(action, tmp_path / "a.blend")
    play = AC.play_and_measure(tmp_path / "a.blend", ra, compat, "child_average")
    assert play["max_slide_cm"] <= AC.MAX_SLIDE_CM["bvh"] and play["leg_stretch"] <= 1.001
    assert play["lowest_foot_below_rest_m"] <= AC.MAX_SINK_M  # grounded: no foot point in the floor
    assert play["control_motion_deg"]["thigh_fk.L"] > 20  # the legs really walk


def test_compat_refuses_another_rig_and_undeclared_bodies():
    from studio_contracts.animation import compatibility_issues

    c = AnimationCompat(rig_profile="cartoon_humanoid@1.0", category="gesture", frames=24, loops=False, travels=False,
                        controls=("head",), fit_profiles=("child_average",), reference_leg_length_m=0.4)
    assert compatibility_issues(c, "cartoon_humanoid@1.0", "child_average") == []
    assert compatibility_issues(c, "quadruped@1.0", "child_average")
    assert compatibility_issues(c, "cartoon_humanoid@2.0", "child_average")
    assert compatibility_issues(c, "cartoon_humanoid@1.0", "adult_average")
    bad = c.model_copy(update={"controls": ("head", "tail_fk.001"), "fit_profiles": ()})
    assert "tail_fk.001" in compatibility_issues(bad, "cartoon_humanoid@1.0", "adult_average")[0]


def test_generate_and_publish_animation_jobs(tmp_path):
    spec = gen_spec("walk")
    ws = tmp_path / "gen"
    ws.mkdir()
    proc, result = run_job(ws, "generate_animation", {"spec": spec.model_dump(mode="json"), "previews": FAST})
    assert result["status"] == "succeeded", (result.get("error"), proc.stderr[-3000:])
    data = result["data"]
    assert data["check"]["ok"], data["report"]["failed"]
    assert {o["path"] for o in result["outputs"]} >= {"animation.blend", "animation.json", "check_report.json",
                                                       "frames.png", "thumbnail.png"}
    report = json.loads((ws / "out" / "check_report.json").read_text())
    assert [b["fit_profile"] for b in report["bodies"]] == ["child_average", "adult_average"]
    blend = ws / "out" / "animation.blend"

    pub = tmp_path / "pub"
    (pub / "inputs").mkdir(parents=True)
    shutil.copy(blend, pub / "inputs" / "animation.blend")
    inputs = [{"path": "inputs/animation.blend", "sha256": sha(blend)}]
    params = {"version_id": "00000000-0000-7000-8000-000000000001", "spec": spec.model_dump(mode="json"),
              "compat": data["compat"]}
    _, result = run_job(pub, "publish_animation", params, inputs)
    assert result["status"] == "succeeded", result.get("error")
    assert result["data"]["report"]["ok"], result["data"]["report"]["failed"]
    # a compat record that does not match the file is caught
    params["compat"] = {**data["compat"], "stride_m": 9.0}
    pub2 = tmp_path / "pub2"
    (pub2 / "inputs").mkdir(parents=True)
    shutil.copy(blend, pub2 / "inputs" / "animation.blend")
    _, result = run_job(pub2, "publish_animation", params, inputs)
    assert "compat_matches_draft" in result["data"]["report"]["failed"]


def test_walk_bakes_deterministically(tmp_path):
    ra = resolve_animation(gen_spec("walk", params={"stride": 0.8}))
    vals = []
    for _ in range(2):
        blend_io.reset_empty()
        _, parts = AC.reference_body("child_average", ra.style_profile)
        action, _, _ = AC.make_action(ra, parts.rig, tmp_path)
        vals.append(sorted((fc.data_path, fc.array_index, tuple(round(k.co[1], 6) for k in fc.keyframe_points))
                           for fc in G.fcurves(action)))
    assert vals[0] == vals[1]
