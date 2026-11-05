"""Props and environments in Blender (Phase 7): every catalog prop and template built and
measured, the four jobs as the worker runs them, and the measured checks catching a set
that was moved after the contract placed it."""

import json
from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")

import importlib.util
import sys

_spec = importlib.util.spec_from_file_location("outfit_helpers", Path(__file__).with_name("test_outfit_fit.py"))
_h = sys.modules.setdefault("outfit_helpers", importlib.util.module_from_spec(_spec))
_spec.loader.exec_module(_h)
run_job, sha = _h.run_job, _h.sha

from cartoon_engine.core import blend_io
from cartoon_engine.environment.build import build_environment_file, build_prop_file
from cartoon_engine.environment.check import check_environment, check_prop
from studio_contracts.environment import (
    ENVIRONMENTS,
    PROPS,
    EnvironmentSpecification,
    PropSpecification,
    resolve_environment,
    resolve_prop,
)

FAST = {"resolution": 96, "samples": 1, "glb": True}


def env_spec(key: str, **kw) -> EnvironmentSpecification:
    t = next(e for e in ENVIRONMENTS if e.key == key)
    return EnvironmentSpecification(name=t.label, template={"id": t.id, "version": t.version}, **kw)


def prop_spec(key: str, **kw) -> PropSpecification:
    p = next(p for p in PROPS if p.key == key)
    return PropSpecification(name=p.label, item={"id": p.id, "version": p.version}, **kw)


@pytest.mark.parametrize("key", [p.key for p in PROPS])
def test_every_prop_measures_its_contract_size(key):
    rp = resolve_prop(prop_spec(key, params={"size": 0.8}))
    blend_io.reset_empty()
    _, obj = build_prop_file(rp, rp.digest())
    checks = check_prop(obj, rp)
    assert all(c["ok"] for c in checks), checks


@pytest.mark.parametrize("key", [e.key for e in ENVIRONMENTS])
def test_every_template_passes_the_measured_layout_check(key):
    env = resolve_environment(env_spec(key))
    blend_io.reset_empty()
    coll, lights, info = build_environment_file(env, env.digest())
    report = check_environment(coll, lights, env)
    assert report["ok"], report["failed"]
    names = {o.name for o in coll.objects}
    assert {f"DRESS_{d.id}" for d in env.dressing} <= names and {f"MARK_{m.name}" for m in env.marks} <= names
    assert {f"CAM_{c.name}" for c in env.cameras} <= names
    if env.kind == "room":
        assert bpy.data.objects["WALL_south"].visible_camera is False  # the cutaway
        assert any(c["name"].startswith("door:") for c in report["checks"])


def test_the_measured_check_catches_what_moved_after_validation():
    env = resolve_environment(env_spec("living_room"))
    blend_io.reset_empty()
    coll, lights, _ = build_environment_file(env, env.digest())
    # push the table into the sofa, the lamp onto the centre mark, and lift the ball off the floor
    bpy.data.objects["DRESS_table_1"].location = (-0.3, 1.7, 0.0)
    bpy.data.objects["DRESS_lamp_1"].location = (0.0, 0.0, 0.0)
    bpy.data.objects["DRESS_ball_1"].location.z = 0.2
    failed = set(check_environment(coll, lights, env)["failed"])
    assert {"no_overlap:sofa_1+table_1", "grounded:ball_1", "mark:centre"} <= failed


def test_prop_jobs_build_link_and_publish(tmp_path):
    spec = prop_spec("chair", material={"finish": "painted", "color": "red"}).model_dump(mode="json")
    ws = tmp_path / "gen"
    ws.mkdir()
    proc, result = run_job(ws, "generate_prop", {"spec": spec, "previews": FAST})
    assert result["status"] == "succeeded", proc.stderr[-3000:]
    data = result["data"]
    assert data["check"]["ok"], data["report"]["failed"]
    assert {o["role"] for o in result["outputs"]} == {"blend", "spec", "thumbnail", "glb", "report"}
    blend = ws / "out" / "prop.blend"

    pub = tmp_path / "pub"
    (pub / "inputs").mkdir(parents=True)
    (pub / "inputs" / "prop.blend").write_bytes(blend.read_bytes())
    inputs = [{"path": "inputs/prop.blend", "sha256": sha(blend)}]
    proc, result = run_job(pub, "publish_prop", {"spec": spec}, inputs)
    assert result["status"] == "succeeded", proc.stderr[-3000:]
    assert result["data"]["report"]["ok"], result["data"]["report"]["failed"]
    # a file built from another specification is refused
    other = prop_spec("chair", material={"color": "royal_blue"}).model_dump(mode="json")
    pub2 = tmp_path / "pub2"
    (pub2 / "inputs").mkdir(parents=True)
    (pub2 / "inputs" / "prop.blend").write_bytes(blend.read_bytes())
    proc, result = run_job(pub2, "publish_prop", {"spec": other}, inputs)
    assert result["status"] == "succeeded", proc.stderr[-3000:]
    assert result["data"]["report"]["failed"] == ["built_from_spec"]


def test_environment_jobs_build_link_and_publish(tmp_path):
    spec = env_spec("bedroom", lighting="evening",
                    marks=[{"name": "by_bed", "at": (0.2, 0.4), "facing_deg": -30}]).model_dump(mode="json")
    ws = tmp_path / "gen"
    ws.mkdir()
    proc, result = run_job(ws, "generate_environment", {"spec": spec, "previews": FAST})
    assert result["status"] == "succeeded", proc.stderr[-3000:]
    data = result["data"]
    assert data["check"]["ok"], data["report"]["failed"]
    assert data["lighting"] == "evening" and {m["name"] for m in data["layout"]["marks"]} == {"by_bed", "door"}
    out = ws / "out"
    assert {p.name for p in out.iterdir()} >= {"environment.blend", "environment.json", "wide.png", "plan.png",
                                               "thumbnail.png", "preview.glb", "layout_report.json"}
    assert json.loads((out / "environment.json").read_text())["layout"] == data["layout"]
    blend = out / "environment.blend"

    pub = tmp_path / "pub"
    (pub / "inputs").mkdir(parents=True)
    (pub / "inputs" / "environment.blend").write_bytes(blend.read_bytes())
    inputs = [{"path": "inputs/environment.blend", "sha256": sha(blend)}]
    proc, result = run_job(pub, "publish_environment", {"spec": spec}, inputs)
    assert result["status"] == "succeeded", proc.stderr[-3000:]
    assert result["data"]["report"]["ok"], result["data"]["report"]["failed"]
    assert result["data"]["stamps"]["studio_lighting"] == "evening"
    # a staged file that does not match its recorded hash is refused outright
    bad = [{"path": "inputs/environment.blend", "sha256": "0" * 64}]
    _, result = run_job(pub, "publish_environment", {"spec": spec}, bad)
    assert result["status"] == "failed" and "SHA-256" in result["error"]
