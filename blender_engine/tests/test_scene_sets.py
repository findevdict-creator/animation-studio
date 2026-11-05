"""Scenes in a set (Phase 7) in Blender: two characters in outfits, placed at the
environment's marks, with props on the floor, on the set's table and on a scene prop, under
an overridden lighting preset; assembled with a preview video, reopened in another
workspace and checked; and a camera mark scene. The sources never change."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")

from studio_contracts.environment import (
    EnvironmentSpecification,
    PropSpecification,
    get_prop_def,
    resolve_environment,
    resolve_prop,
)
from studio_contracts.ids import uuid7
from studio_contracts.scene import (
    PropFacts,
    ResolvedDialogueLine,
    ResolvedInstance,
    ResolvedPin,
    ResolvedPropUse,
    ResolvedScene,
    SceneManifest,
    check_layout,
    pin_path,
    prop_heights,
    resolve_clip,
    start_transform,
    validate_manifest,
)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = sys.modules.get(name) or importlib.util.module_from_spec(spec)
    if name not in sys.modules:
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


_o = _load("test_outfit_fit")
ALI, run_job, save_character, save_outfit, sha = _o.ALI, _o.run_job, _o.save_character, _o.save_outfit, _o.sha
FAST = {"width": 128, "height": 72, "samples": 1}
ENV_SPEC = EnvironmentSpecification(name="Home", template={"id": "ENVD-LIVING_ROOM", "version": "0.1"},
                                    marks=[{"name": "rug", "at": (-0.6, -0.8), "facing_deg": -20},
                                           {"name": "window", "at": (1.2, -0.3), "facing_deg": 30}])
PROPS = {"PROP-001": ("ball", {}), "PROP-002": ("books", {}), "PROP-003": ("table", {"params": {"size": 0.0}}),
         "PROP-004": ("apple", {})}


def save_env(path: Path) -> Path:
    from cartoon_engine.core import blend_io
    from cartoon_engine.environment.build import build_environment_file

    env = resolve_environment(ENV_SPEC)
    blend_io.reset_empty()
    coll, lights, _ = build_environment_file(env, env.digest())
    bpy.data.libraries.write(str(path), {coll, lights}, fake_user=True)
    return path


def save_prop(path: Path, key: str, extra: dict):
    from cartoon_engine.core import blend_io
    from cartoon_engine.environment.build import build_prop_file

    d = get_prop_def(key)
    rp = resolve_prop(PropSpecification(name=key, item={"id": d.id, "version": d.version}, **extra))
    blend_io.reset_empty()
    coll, _ = build_prop_file(rp, rp.digest())
    bpy.data.libraries.write(str(path), {coll}, fake_user=True)
    return path, rp


def stage(root: Path):
    pins, facts = {}, {}

    def pin(kind, code, name, files):
        d = root / pin_path(kind, code, "1.0")
        d.mkdir(parents=True, exist_ok=True)
        out = {}
        for fname, src in files.items():
            (d / fname).write_bytes(Path(src).read_bytes())
            (d / fname).chmod(0o444)
            out[fname] = sha(d / fname)
        pins[code] = ResolvedPin(kind=kind, code=code, version="1.0", version_id=str(uuid7()), status="published",
                                 name=name, path=pin_path(kind, code, "1.0"), files=out)

    tmp = root / "_src"
    tmp.mkdir()
    cb, cj = save_character(tmp / "ali", ALI)
    pin("character", "CHAR-001", "Ali", {"character.blend": cb, "character.json": cj})
    pin("outfit", "OUTFIT-001", "School", {"outfit.blend": save_outfit(tmp / "school.blend", "school")})
    pin("outfit", "OUTFIT-002", "Winter", {"outfit.blend": save_outfit(tmp / "winter.blend", "winter")})
    pin("environment", "ENV-001", "Home", {"environment.blend": save_env(tmp / "env.blend")})
    for code, (key, extra) in PROPS.items():
        path, rp = save_prop(tmp / f"{key}.blend", key, extra)
        pin("prop", code, key, {"prop.blend": path})
        facts[f"{code}@1.0"] = PropFacts(size=rp.size, solid=rp.solid, rests_on=rp.rests_on, top_z=rp.surface_z("top"))
    shutil.rmtree(tmp)
    return pins, facts


def resolve(m: SceneManifest, pins, facts) -> ResolvedScene:
    """What the API does (``app.assets.scenes.resolve``) with the registry replaced by ``pins``."""
    from studio_contracts.rig_profile import CARTOON_HUMANOID_V1 as RIG

    layout = resolve_environment(ENV_SPEC).layout() if m.environment else None
    issues = [i for i in validate_manifest(m) + check_layout(m, layout, facts) if i.severity == "error"]
    assert not issues, issues
    heights = prop_heights(m, layout, facts)
    insts = [ResolvedInstance(
        key=inst.key, character=pins[inst.character.code], rig_profile=RIG.ref, fit_profile="child_average",
        outfit=pins[inst.outfit.code] if inst.outfit else None, outfit_compatibility={"status": "approved"},
        accessories=(), transform=start_transform(inst, layout), expressions=tuple(inst.expressions),
        blink=inst.blink, animations=tuple(resolve_clip(a) for a in inst.animations),
        dialogue=tuple(ResolvedDialogueLine(start_frame=d.start_frame, end_frame=d.end_frame, text=d.text)
                       for d in inst.dialogue), state=inst.state) for inst in m.instances]
    props = tuple(ResolvedPropUse(key=u.key, pin=pins[u.prop.code], item=get_prop_def(pins[u.prop.code].name).ref,
                                  size=facts[u.prop.ref].size, at=u.at, z=heights[u.key], rotation_deg=u.rotation_deg,
                                  on=u.on) for u in m.props)
    return ResolvedScene(manifest_digest=m.digest(), name=m.name, fps=m.fps, frame_start=m.frame_start,
                         frame_end=m.frame_end, camera=m.camera, instances=tuple(insts),
                         environment=pins["ENV-001"] if m.environment else None, layout=layout, props=props,
                         lighting=m.lighting or (layout or {}).get("lighting"))


def p(code):
    return {"code": code, "version": "1.0"}


MANIFEST = SceneManifest.model_validate({
    "name": "Living room chat", "frame_start": 1, "frame_end": 24,
    "environment": p("ENV-001"), "lighting": "evening",
    "instances": [
        {"key": "ali_school", "character": p("CHAR-001"), "outfit": p("OUTFIT-001"), "mark": "rug",
         "animations": [{"clip": "wave", "start_frame": 1}],
         "dialogue": [{"start_frame": 6, "end_frame": 18, "text": "Want to play?"}]},
        {"key": "ali_winter", "character": p("CHAR-001"), "outfit": p("OUTFIT-002"), "mark": "window",
         "transform": {"location": (0.1, 0.0, 0.0)}, "animations": [{"clip": "CLIP-WALK@0.1", "start_frame": 1}]},
    ],
    "props": [
        {"key": "ball", "prop": p("PROP-001"), "at": (0.3, -1.4)},
        {"key": "books", "prop": p("PROP-002"), "at": (-0.4, 0.75), "on": "table_1", "rotation_deg": 20},
        {"key": "side_table", "prop": p("PROP-003"), "at": (2.2, -1.6), "rotation_deg": 90},
        {"key": "apple", "prop": p("PROP-004"), "at": (2.2, -1.5), "on": "side_table"},
    ],
    "camera": {"preset": "wide", "angle_deg": 25},
})


def stage_reopen(ws: Path, dst: Path, inputs):
    for i in inputs:
        t = dst / i["path"]
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ws / i["path"], t)
    (dst / "scene").mkdir()
    shutil.copyfile(ws / "out" / "scene.blend", dst / "scene" / "scene.blend")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    ws = tmp_path_factory.mktemp("set_scene")
    pins, facts = stage(ws)
    rs = resolve(MANIFEST, pins, facts)
    inputs = [{"path": f"{q.path}/{n}", "sha256": s} for q in rs.pins() for n, s in q.files.items()]
    params = {"scene_version_id": str(uuid7()), "resolved": rs.model_dump(mode="json"),
              "previews": {**FAST, "video_every": 6}}
    proc, result = run_job(ws, "assemble_scene", params, inputs)
    return ws, rs, inputs, proc, result, pins, facts


def test_assemble_in_a_set(built):
    ws, rs, _, proc, result, _, _ = built
    assert result["status"] == "succeeded", proc.stdout[-4000:] + str(result.get("error"))
    report = json.loads((ws / "out" / "scene_report.json").read_text())
    assert report["ok"], report["failed"]
    assert report["sources"]["unchanged"]
    stored = {x["stored"] for x in report["libraries"]["libraries"]}
    assert {"//../assets/environment/ENV-001/1.0/environment.blend",
            *(f"//../assets/prop/PROP-00{i}/1.0/prop.blend" for i in range(1, 5))} <= stored
    s = report["set"]
    assert s["environment"]["lighting_override"] and s["environment"]["lighting_from_file"] == "indoor_day"
    assert {x["key"]: x["z"] for x in s["props"]} == {"ball": 0.0, "books": 0.4752, "side_table": 0.0,
                                                       "apple": 0.576}
    assert report["video"]["frames"] == 4 and (ws / "out" / "preview.mp4").stat().st_size > 1000
    assert {o["path"] for o in result["outputs"]} >= {"preview.mp4", "frames.png", "scene.blend"}
    rug = next(i for i in rs.instances if i.key == "ali_school").transform
    assert rug.location == (-0.6, -0.8, 0.0) and rug.rotation_deg[2] == -20


def test_reopen_checks_the_set(built, tmp_path):
    ws, rs, inputs, _, result, _, _ = built
    assert result["status"] == "succeeded"
    stage_reopen(ws, tmp_path, inputs)
    params = {"scene_version_id": str(uuid7()), "resolved": rs.model_dump(mode="json"),
              "scene_blend_sha256": sha(ws / "out" / "scene.blend"), "previews": FAST}
    proc, res = run_job(tmp_path, "verify_scene", params, inputs + [{"path": "scene/scene.blend",
                                                                     "sha256": params["scene_blend_sha256"]}])
    assert res["status"] == "succeeded", proc.stdout[-4000:] + str(res.get("error"))
    report = res["data"]["report"]
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]
    names = {c["name"] for c in report["checks"]}
    assert {"set:environment_linked_from_pin", "set:lighting", "prop:books:grounded", "prop:apple:grounded",
            "prop:side_table:clear", "prop:ball:inside", "path:ali_school", "path:ali_winter",
            "ali_winter:placement"} <= names


def test_moved_props_and_a_walk_into_the_sofa_fail_the_set_check(built):
    from cartoon_engine.core import blend_io
    from cartoon_engine.scene.set_dressing import set_checks

    ws, rs, _, _, result, _, _ = built
    assert result["status"] == "succeeded"
    blend_io.open_file(ws / "out" / "scene.blend")
    scene = bpy.context.scene
    bpy.data.objects["PROPI_side_table"].location = (-0.3, 1.5, 0.0)  # into the sofa
    bpy.data.objects["PROPI_books"].location.z = 0.0  # fell off the table
    sofa = [(1, -1.0, -0.5), (12, -0.3, 1.8)]  # a root path that ends inside the sofa
    failed = {c["name"] for c in set_checks(rs, scene, ws, {"ali_school": sofa}) if not c["ok"]}
    assert {"prop:side_table:clear", "prop:books:grounded", "path:ali_school"} <= failed


def test_a_camera_mark_scene(built, tmp_path):
    ws, _, _, _, result, pins, facts = built
    assert result["status"] == "succeeded"
    m = SceneManifest.model_validate({**MANIFEST.model_dump(mode="json"), "name": "From the front",
                                      "lighting": None, "camera": {"mark": "front"}, "props": []})
    rs = resolve(m, pins, facts)
    w2 = tmp_path / "ws"
    for q in rs.pins():
        for n in q.files:
            t = w2 / q.path / n
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ws / q.path / n, t)
    inputs = [{"path": f"{q.path}/{n}", "sha256": s} for q in rs.pins() for n, s in q.files.items()]
    proc, res = run_job(w2, "assemble_scene", {"scene_version_id": str(uuid7()), "resolved": rs.model_dump(mode="json"),
                                               "previews": FAST}, inputs)
    assert res["status"] == "succeeded", proc.stdout[-4000:] + str(res.get("error"))
    report = res["data"]["report"]
    assert report["camera"]["name"] == "CAM_front" and report["set"]["environment"]["lighting_override"] is False
