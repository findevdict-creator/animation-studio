"""Scene assembly in Blender (SRS Phase 5): the same character twice in one scene in two
different outfits with accessories, expressions, clips and dialogue; saved, reopened in a
different workspace, checked against the manifest; the sources never change."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")

from studio_contracts.ids import uuid7
from studio_contracts.scene import (
    ResolvedAccessoryUse,
    ResolvedDialogueLine,
    ResolvedInstance,
    ResolvedPin,
    ResolvedScene,
    SceneManifest,
    pin_path,
    resolve_clip,
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
_a = _load("test_accessories")
ALI, run_job, save_character, save_outfit, sha = _o.ALI, _o.run_job, _o.save_character, _o.save_outfit, _o.sha
acc_spec, save_accessory = _a.acc_spec, _a.save_accessory
FAST = {"width": 96, "height": 54, "samples": 1}


def stage(root: Path) -> dict[str, ResolvedPin]:
    """Pinned files laid out as the worker stages them; returns pins by name."""
    pins = {}

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
    for i, key in enumerate(("glasses", "watch", "beanie")):
        pin("accessory", f"ACC-00{i + 1}", key, {"accessory.blend": save_accessory(tmp / f"{key}.blend", acc_spec(key))})
    shutil.rmtree(tmp)
    return pins


def resolve(m: SceneManifest, pins: dict[str, ResolvedPin]) -> ResolvedScene:
    from studio_contracts.accessory import get_accessory_def
    from studio_contracts.rig_profile import CARTOON_HUMANOID_V1 as RIG

    assert not [i for i in validate_manifest(m) if i.severity == "error"]
    insts = []
    for inst in m.instances:
        accs = []
        for use in inst.accessories:
            p = pins[use.accessory.code]
            d = get_accessory_def(p.name)
            point = use.attach_point or d.attach_points[0]
            accs.append(ResolvedAccessoryUse(pin=p, attach_point=point, attach_bone=RIG.attachment_points[point],
                                             item=d.ref, placement=d.placement))
        insts.append(ResolvedInstance(
            key=inst.key, character=pins[inst.character.code], rig_profile=RIG.ref, fit_profile="child_average",
            outfit=pins[inst.outfit.code] if inst.outfit else None, outfit_compatibility={"status": "approved"},
            accessories=tuple(accs), transform=inst.transform, expressions=tuple(inst.expressions), blink=inst.blink,
            animations=tuple(resolve_clip(a) for a in inst.animations), dialogue=tuple(ResolvedDialogueLine(start_frame=d.start_frame, end_frame=d.end_frame, text=d.text)
                           for d in inst.dialogue),
            state=inst.state))
    return ResolvedScene(manifest_digest=m.digest(), name=m.name, fps=m.fps, frame_start=m.frame_start,
                         frame_end=m.frame_end, camera=m.camera, instances=tuple(insts))


MANIFEST = SceneManifest.model_validate({
    "name": "Two Alis", "frame_start": 1, "frame_end": 48,
    "instances": [
        {"key": "ali_school", "character": {"code": "CHAR-001", "version": "1.0"},
         "outfit": {"code": "OUTFIT-001", "version": "1.0"},
         "accessories": [{"accessory": {"code": "ACC-001", "version": "1.0"}},
                         {"accessory": {"code": "ACC-002", "version": "1.0"}, "attach_point": "wrist_R"}],
         "transform": {"location": [-0.45, 0, 0], "rotation_deg": [0, 0, 15]},
         "expressions": [{"frame": 1, "expression": "neutral"}, {"frame": 20, "expression": "happy"},
                         {"frame": 40, "expression": "surprised", "weight": 0.6}],
         "animations": [{"clip": "wave", "start_frame": 1, "repeat": 2}, {"clip": "CLIP-IDLE@0.1", "start_frame": 1}],
         "dialogue": [{"start_frame": 24, "end_frame": 36, "text": "Hi! Look at my cap."}],
         "state": {"mood": "cheerful"}},
        {"key": "ali_winter", "character": {"code": "CHAR-001", "version": "1.0"},
         "outfit": {"code": "OUTFIT-002", "version": "1.0"},
         "accessories": [{"accessory": {"code": "ACC-003", "version": "1.0"}}],
         "transform": {"location": [0.45, 0.1, 0], "rotation_deg": [0, 0, -15], "scale": 1.0},
         "expressions": [{"frame": 10, "expression": "sad"}, {"frame": 30, "expression": "laughing"}],
         "animations": [{"clip": "look_around", "start_frame": 5}], "blink": False},
    ],
    "camera": {"preset": "wide", "angle_deg": 15},
})


@pytest.fixture(scope="module")
def assembled(tmp_path_factory):
    ws = tmp_path_factory.mktemp("assemble")
    pins = stage(ws)
    rs = resolve(MANIFEST, pins)
    inputs = [{"path": f"{p.path}/{n}", "sha256": s} for p in rs.pins() for n, s in p.files.items()]
    params = {"scene_version_id": str(uuid7()), "resolved": rs.model_dump(mode="json"), "previews": FAST}
    proc, result = run_job(ws, "assemble_scene", params, inputs)
    return ws, rs, inputs, proc, result


def test_assemble_scene_job(assembled):
    ws, rs, _, proc, result = assembled
    assert proc.returncode == 0 and result["status"] == "succeeded", proc.stdout[-4000:] + str(result.get("error"))
    roles = {o["path"]: o["role"] for o in result["outputs"]}
    assert roles == {"scene.blend": "blend", "scene_report.json": "report", "frames.png": "preview",
                     "thumbnail.png": "thumbnail"}
    report = json.loads((ws / "out" / "scene_report.json").read_text())
    assert report["ok"], report["failed"]
    assert report["sources"]["unchanged"] and report["sources"]["before"] == report["sources"]["after"]
    stored = sorted(x["stored"] for x in report["libraries"]["libraries"])
    assert stored == ["//../assets/accessory/ACC-001/1.0/accessory.blend",
                      "//../assets/accessory/ACC-002/1.0/accessory.blend",
                      "//../assets/accessory/ACC-003/1.0/accessory.blend",
                      "//../assets/character/CHAR-001/1.0/character.blend",
                      # appended (fitting moves vertices), so only the library record of the source stays
                      "//../assets/outfit/OUTFIT-001/1.0/outfit.blend",
                      "//../assets/outfit/OUTFIT-002/1.0/outfit.blend"]
    school, winter = report["instances"]
    assert school["character"]["override"] != winter["character"]["override"]
    assert school["outfit"]["source"].startswith("OUTFIT-001@1.0#") and winter["outfit"]["source"].startswith("OUTFIT-002@1.0#")
    assert {a["point"] for a in school["accessories"]} == {"face_eyes", "wrist_R"}
    assert school["accessory_check"]["ok"] and winter["accessory_check"]["ok"]
    assert [a["clip"] for a in school["animations"]] == ["CLIP-WAVE@0.1", "CLIP-IDLE@0.1"]
    assert school["face"]["expressions"] == [[1, "neutral", 1.0], [20, "happy", 1.0], [40, "surprised", 0.6]]
    assert school["face"]["blinks"] and not winter["face"]["blinks"]
    assert school["state"] == {"mood": "cheerful"}


def test_reopen_in_another_workspace(assembled, tmp_path):
    ws, rs, inputs, _, result = assembled
    assert result["status"] == "succeeded"
    # the workspace is rebuilt from the manifest: pinned files and the stored scene file only
    for i in inputs:
        dst = tmp_path / i["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ws / i["path"], dst)
    (tmp_path / "scene").mkdir()
    shutil.copyfile(ws / "out" / "scene.blend", tmp_path / "scene" / "scene.blend")
    params = {"scene_version_id": str(uuid7()), "resolved": rs.model_dump(mode="json"),
              "scene_blend_sha256": sha(ws / "out" / "scene.blend"), "previews": FAST}
    proc, res = run_job(tmp_path, "verify_scene", params, inputs + [{"path": "scene/scene.blend",
                                                                     "sha256": params["scene_blend_sha256"]}])
    assert proc.returncode == 0 and res["status"] == "succeeded", proc.stdout[-4000:] + str(res.get("error"))
    report = res["data"]["report"]
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]
    names = {c["name"] for c in report["checks"]}
    assert {"ali_school:character_is_a_library_override", "ali_school:outfit_bound_to_this_rig",
            "ali_school:accessory:ACC-002", "ali_winter:expressions_play", "ali_school:dialogue_mouth_moves",
            "ali_winter:placement", "sources_unchanged", "libraries_resolve"} <= names
    assert res["data"]["sources_unchanged"]


def test_a_changed_source_fails_the_reopen(assembled, tmp_path):
    ws, rs, inputs, _, result = assembled
    for i in inputs:
        dst = tmp_path / i["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ws / i["path"], dst)
    (tmp_path / "scene").mkdir()
    shutil.copyfile(ws / "out" / "scene.blend", tmp_path / "scene" / "scene.blend")
    with open(tmp_path / inputs[0]["path"], "ab") as f:
        f.write(b"tampered")
    params = {"scene_version_id": str(uuid7()), "resolved": rs.model_dump(mode="json"),
              "scene_blend_sha256": sha(ws / "out" / "scene.blend"), "previews": FAST}
    _, res = run_job(tmp_path, "verify_scene", params, inputs)
    assert res["status"] == "failed" and "does not match its pin" in res["error"]
