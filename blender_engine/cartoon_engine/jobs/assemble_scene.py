"""``assemble_scene``: a resolved scene manifest -> ``scene.blend`` (SRS 16.2, Phase 5).

The worker has staged every pinned file at ``assets/<kind>/<code>/<version>/`` and checked
each SHA-256 against the registry. Here, per instance: link and override the character,
append and fit the outfit, link and attach the accessories, check them on this body, assign
clips and the face track, place it. Then a camera, ``out/scene.blend`` saved with relative
library paths (``//../assets/...``), the library check, and the post-condition that every
staged source file still hashes as it did: a scene can never change an asset.

Previews are rendered after the save from the same session: a contact sheet of a few
frames, a thumbnail and, when asked, ``preview.mp4`` of every Nth frame. In Phase 7 the
scene's set (environment, props, lighting, camera mark) is added after the instances. The
reopen check is its own job (``verify_scene``).
"""

from __future__ import annotations

import json
import time
from typing import Any

from studio_contracts.jobs import AssembleSceneParams
from studio_contracts.scene import ResolvedScene

import cartoon_engine
from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json


def preview_frames(rs: ResolvedScene, frames: list[int] | None) -> list[int]:
    if frames:
        return [min(max(f, rs.frame_start), rs.frame_end) for f in frames]
    a, b = rs.frame_start, rs.frame_end
    return sorted({a, a + (b - a) // 3, a + 2 * (b - a) // 3, b})


def run(ctx: JobContext) -> dict[str, Any]:
    from cartoon_engine.scene import files
    from cartoon_engine.scene.assemble import (
        assemble,
        contact_sheet,
        render_frames,
        stage_camera,
    )

    timings: dict[str, float] = {}
    t0 = time.monotonic()

    def lap(name: str) -> None:
        nonlocal t0
        timings[name] = round(time.monotonic() - t0, 2)
        t0 = time.monotonic()

    params = AssembleSceneParams.model_validate(ctx.envelope.params)
    rs = ResolvedScene.model_validate(params.resolved)
    ws, out = ctx.workspace, ctx.out_dir
    staged = {i.path: i.sha256 for i in ctx.envelope.inputs}
    expected = files.expected_files(rs)
    missing = sorted(p for p in expected if staged.get(p) != expected[p])
    if missing:
        raise RuntimeError(f"pinned files not staged as recorded: {missing}")
    before = files.hash_sources(ws, rs)
    if before != expected:
        raise RuntimeError("a staged file does not match its pin before assembly")
    lap("check_inputs_s")

    built, scene = assemble(ws, rs, progress=lambda f, m: ctx.progress(0.05 + 0.5 * f, m))
    lap("assemble_s")
    ctx.progress(0.58, "camera and light")
    stage_camera(scene, built, rs)
    blend = out / "scene.blend"
    ctx.progress(0.62, "saving scene.blend")
    blend_io.save(blend)
    libs = files.library_check(out, ws, rs)
    ok_after, changed = files.unchanged(ws, rs)
    lap("save_check_s")
    if not ok_after:
        raise RuntimeError(f"source files changed during assembly: {changed}")
    if not libs["ok"]:
        raise RuntimeError(f"scene.blend links outside the pinned assets: {libs['outside_or_absolute']}")

    ctx.progress(0.7, "rendering preview frames")
    frames = preview_frames(rs, params.previews.frames)
    tmp = ws / "tmp" / "frames"
    tmp.mkdir(parents=True, exist_ok=True)
    paths = render_frames(scene, tmp, frames, (params.previews.width, params.previews.height), params.previews.samples)
    contact_sheet(paths, out / "frames.png")
    (out / "thumbnail.png").write_bytes(paths[0].read_bytes())
    ctx.output(out / "frames.png", "preview", "image/png")
    ctx.output(out / "thumbnail.png", "thumbnail", "image/png")
    video = None
    if params.previews.video_every:
        from cartoon_engine.character.previews import encode_mp4

        step = params.previews.video_every
        vframes = list(range(rs.frame_start, rs.frame_end + 1, step))
        ctx.progress(0.8, f"rendering {len(vframes)} frames for preview.mp4")
        vdir = ws / "tmp" / "video"
        vdir.mkdir(parents=True, exist_ok=True)
        vpaths = render_frames(scene, vdir, vframes, (params.previews.width, params.previews.height),
                               params.previews.samples)
        encode_mp4(vpaths, out / "preview.mp4", max(1, round(rs.fps / step)), params.previews.width,
                   params.previews.height)
        ctx.output(out / "preview.mp4", "preview", "video/mp4")
        video = {"frames": len(vframes), "every": step, "fps": max(1, round(rs.fps / step)),
                 "bytes": (out / "preview.mp4").stat().st_size}
    lap("previews_s")
    # rendering must not have touched the sources either
    ok_final, changed = files.unchanged(ws, rs)
    if not ok_final:
        raise RuntimeError(f"source files changed while rendering: {changed}")

    instances = [b.report for b in built]
    acc_failed = [f"{b.key}:{n}" for b in built for n in b.report.get("accessory_check", {}).get("failed", [])]
    doc = {"scene": rs.name, "manifest_digest": rs.manifest_digest, "scene_blend_sha256": files.sha256(blend),
           "fps": rs.fps, "frame_start": rs.frame_start, "frame_end": rs.frame_end, "instances": instances,
           "libraries": libs, "sources": {"before": before, "after": files.hash_sources(ws, rs),
                                          "unchanged": ok_final},
           "preview_frames": frames, "video": video, "set": json.loads(scene.get("studio_set_report", "null")),
           "camera": {"name": scene.camera.name, "lens_mm": round(scene.camera.data.lens, 2),
                      "kept_inside": json.loads(scene.camera.get("studio_kept_inside", "null"))},
           "ok": ok_final and libs["ok"] and not acc_failed, "failed": acc_failed,
           "blender_version": blend_io.blender_version(), "engine_version": cartoon_engine.__version__,
           "timings_s": timings}
    write_json(out / "scene_report.json", doc)
    ctx.output(blend, "blend", "application/x-blender")
    ctx.output(out / "scene_report.json", "report", "application/json")
    ctx.progress(1.0, "assembled" if doc["ok"] else "assembled with failed accessory checks")
    return {"scene_blend_sha256": doc["scene_blend_sha256"], "ok": doc["ok"], "failed": acc_failed,
            "instances": len(instances), "libraries": len(libs["libraries"]), "sources_unchanged": ok_final,
            "report": doc, "timings_s": timings}
