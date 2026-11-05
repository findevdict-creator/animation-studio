"""Shots in Blender (Phase 8): a scene from a camera mark with a push-in and a framed scene
with a pan are assembled with their camera keyed, reopened and checked, and rendered to PNG
frames with the series' render settings by ``render_scene``; the same frames come out of a
second render of the same stored file. Cycles records the device it chose; Eevee renders where
libEGL loads (Mesa's software EGL in the worker image) and is refused clearly where it does not."""

import hashlib
import importlib.util
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

bpy = pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")

from studio_contracts.ids import uuid7
from studio_contracts.scene import SceneManifest


def _load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = sys.modules.get(name) or importlib.util.module_from_spec(spec)
    if name not in sys.modules:
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


_s = _load("test_scene_sets")
built = _s.built  # the Phase 7 set scene's staged pins (module fixture)
run_job, sha, resolve, MANIFEST, FAST = _s.run_job, _s.sha, _s.resolve, _s.MANIFEST, _s.FAST
SETTINGS = {"width": 160, "height": 90, "samples": 1, "view_transform": "Standard"}


def pixels(path: Path) -> np.ndarray:
    img = bpy.data.images.load(str(path))
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)


def assemble(built, tmp_path, name, camera, frame_end=24):
    ws, _, _, _, result, pins, facts = built
    assert result["status"] == "succeeded"
    m = SceneManifest.model_validate({**MANIFEST.model_dump(mode="json"), "name": name, "frame_end": frame_end,
                                      "camera": camera, "props": []})
    rs = resolve(m, pins, facts)
    w2 = tmp_path / "assemble"
    for q in rs.pins():
        for n in q.files:
            t = w2 / q.path / n
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ws / q.path / n, t)
    inputs = [{"path": f"{q.path}/{n}", "sha256": s} for q in rs.pins() for n, s in q.files.items()]
    proc, res = run_job(w2, "assemble_scene", {"scene_version_id": str(uuid7()), "resolved": rs.model_dump(mode="json"),
                                               "previews": FAST}, inputs)
    assert res["status"] == "succeeded", proc.stdout[-4000:] + str(res.get("error"))
    return w2, rs, inputs, res["data"]["report"]


def workspace(src: Path, dst: Path, inputs) -> Path:
    for i in inputs:
        t = dst / i["path"]
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / i["path"], t)
    (dst / "scene").mkdir()
    shutil.copyfile(src / "out" / "scene.blend", dst / "scene" / "scene.blend")
    return dst


def scene_inputs(src: Path, inputs):
    return [*inputs, {"path": "scene/scene.blend", "sha256": sha(src / "out" / "scene.blend")}]


def render(src, rs, inputs, dst, **kw):
    w = workspace(src, dst, inputs)
    params = {"render_job_id": str(uuid7()), "scene_version_id": str(uuid7()), "resolved": rs.model_dump(mode="json"),
              "scene_blend_sha256": sha(src / "out" / "scene.blend"), "settings": {**SETTINGS, **kw.pop("settings", {})},
              **kw}
    proc, res = run_job(w, "render_scene", params, scene_inputs(src, inputs))
    return w, proc, res


def test_a_push_in_on_a_camera_mark_is_keyed_reopened_and_rendered(built, tmp_path):
    src, rs, inputs, report = assemble(built, tmp_path, "Push in", {"mark": "wide", "movement": "push_in"})
    move = report["camera"]["move"]
    assert move["movement"] == "push_in" and move["camera"] == "CAM_move" and 0.1 < move["distance_m"] <= 0.8
    assert report["camera"]["name"] == "CAM_move"

    w = workspace(src, tmp_path / "reopen", inputs)
    proc, res = run_job(w, "verify_scene", {"scene_version_id": str(uuid7()), "resolved": rs.model_dump(mode="json"),
                                             "scene_blend_sha256": sha(src / "out" / "scene.blend"), "previews": FAST},
                        scene_inputs(src, inputs))
    assert res["status"] == "succeeded", proc.stdout[-3000:]
    checks = {c["name"]: c for c in res["data"]["report"]["checks"]}
    assert checks["camera_move"]["ok"] and checks["camera_move"]["moved_m"] == pytest.approx(move["distance_m"], abs=1e-3)

    w1, proc, res = render(src, rs, inputs, tmp_path / "render1", frame_start=1, frame_end=3)
    assert res["status"] == "succeeded", proc.stdout[-4000:] + str(res.get("error"))
    data = res["data"]
    assert data["frames"] == 3 and data["in_force"]["engine"] == "CYCLES" and data["in_force"]["resolution"] == [160, 90]
    assert data["in_force"]["device"] in ("OPTIX", "CUDA", "CPU") and data["in_force"]["samples_requested"] == 1
    if data["in_force"]["device"] == "CPU":
        assert data["in_force"]["samples"] == 1  # the CPU keeps the requested samples
    outs = [o for o in res["outputs"] if o["role"] == "frame"]
    assert [o["path"] for o in outs] == ["frames/frame_0001.png", "frames/frame_0002.png", "frames/frame_0003.png"]
    first = {f["frame"]: f["sha256"] for f in data["report"]["frames"]}
    assert first[1] != first[3]  # the camera moved
    assert data["report"]["sources"]["unchanged"]
    png = (w1 / "out" / "frames" / "frame_0002.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and hashlib.sha256(png).hexdigest() == first[2]

    # Cycles on many threads with denoising is not bit-exact, so the second render of the same
    # stored file is compared by its pixels
    w2, proc, again = render(src, rs, inputs, tmp_path / "render2", frame_start=1, frame_end=3)
    assert again["status"] == "succeeded"
    for f in (1, 2, 3):
        a, b = pixels(w1 / "out" / "frames" / f"frame_{f:04d}.png"), pixels(w2 / "out" / "frames" / f"frame_{f:04d}.png")
        assert a.shape == b.shape == (90, 160, 4) and np.abs(a - b).mean() < 0.01


def test_a_pan_and_render_refusals(built, tmp_path):
    src, rs, inputs, report = assemble(built, tmp_path, "Pan", {"preset": "medium", "movement": "pan_right"})
    move = report["camera"]["move"]
    assert move["movement"] == "pan_right" and move["camera"] == "CAM_main"
    (a, b) = move["rotation_deg"]
    assert b[2] - a[2] == pytest.approx(-8.0, abs=0.01)

    from cartoon_engine.jobs.render_scene import eevee_possible

    w, proc, res = render(src, rs, inputs, tmp_path / "eevee", frame_start=1, frame_end=1, settings={"engine": "eevee"})
    if eevee_possible():
        assert res["status"] == "succeeded", proc.stdout[-4000:] + str(res.get("error"))
        assert res["data"]["in_force"]["engine"].startswith("BLENDER_EEVEE") and res["data"]["in_force"]["renderer"]
        assert (w / "out" / "frames" / "frame_0001.png").is_file()
    else:
        assert res["status"] == "failed" and "Eevee" in res["error"]
    _, _, res = render(src, rs, inputs, tmp_path / "fps", settings={"fps": 30})
    assert res["status"] == "failed" and "fps" in res["error"]
    _, _, res = render(src, rs, inputs, tmp_path / "range", frame_start=5, frame_end=99)
    assert res["status"] == "failed" and "outside" in res["error"]
