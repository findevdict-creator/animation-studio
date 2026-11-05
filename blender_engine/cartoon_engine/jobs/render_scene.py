"""``render_scene``: render one shot's frames (SRS 18.1 steps 3 to 6).

The worker stages the scene version's stored ``scene.blend`` at ``scene/scene.blend`` and
every pinned file under ``assets/`` (the same workspace ``verify_scene`` rebuilds), so the
frames come from the pinned versions and nothing else. The job:

* checks ``scene.blend`` and every staged file against their recorded SHA-256;
* opens the file with scripts disabled and checks every library resolves to a pinned file;
* applies the render settings (engine, samples, resolution, colour management) and checks
  the frame rate is the scene's;
* renders each frame of the range to ``out/frames/frame_NNNN.png`` (RGB PNG) and declares
  it a ``frame`` output, reporting progress per frame;
* writes ``render_report.json`` (settings in force, frames with their SHA-256, seconds per
  frame, Blender version) and checks the sources are still byte-identical.

Eevee needs a GPU or EGL context; on a CPU worker without one the job fails with that reason
rather than silently switching engine. Cycles runs anywhere (CPU, denoised).
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from studio_contracts.episode import RenderSettings
from studio_contracts.jobs import RenderSceneParams
from studio_contracts.scene import ResolvedScene

import cartoon_engine
from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json


class EngineUnavailable(RuntimeError):
    pass


def eevee_possible() -> bool:
    """Eevee renders through OpenGL/Vulkan; headless Linux needs libEGL. Without it Blender
    aborts the whole process, so look before rendering."""
    import ctypes
    import sys

    if not sys.platform.startswith("linux"):
        return True
    try:
        ctypes.CDLL("libEGL.so.1")
    except OSError:
        return False
    return True


def apply_settings(scene, s: RenderSettings) -> dict[str, Any]:
    """Put the series' render settings on the scene; returns what is in force."""
    import bpy

    engines = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
    if s.engine == "eevee":
        if not eevee_possible():
            raise EngineUnavailable("Eevee needs a GPU or an EGL context and this worker has neither "
                                    "(libEGL.so.1 cannot be loaded); render with Cycles or on a GPU worker")
        name = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
        scene.render.engine = name
        try:
            scene.eevee.taa_render_samples = s.samples
        except AttributeError:
            pass
    else:
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = s.samples
        scene.cycles.use_adaptive_sampling = True
        try:
            scene.cycles.use_denoising = True
            scene.cycles.denoiser = "OPENIMAGEDENOISE"
        except (TypeError, AttributeError):
            scene.cycles.use_denoising = False
    scene.render.resolution_x, scene.render.resolution_y = s.width, s.height
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 15
    # no date, time or render-time text chunks: a frame's bytes depend on its pixels only
    for name in dir(scene.render):
        if name.startswith("use_stamp"):
            try:
                setattr(scene.render, name, False)
            except (AttributeError, TypeError):
                pass
    scene.render.use_stamp = False
    if s.engine == "cycles":
        scene.cycles.seed = 0
        scene.cycles.use_animated_seed = False
    scene.view_settings.view_transform = s.view_transform
    scene.view_settings.look = s.look if s.look != "None" else "None"
    scene.view_settings.exposure = s.exposure
    scene.view_settings.gamma = s.gamma
    scene.display_settings.display_device = "sRGB"
    return {"engine": scene.render.engine, "samples": s.samples, "resolution": [s.width, s.height],
            "view_transform": scene.view_settings.view_transform, "look": scene.view_settings.look,
            "exposure": s.exposure, "gamma": s.gamma, "fps": scene.render.fps,
            "denoise": bool(getattr(scene.cycles, "use_denoising", False)) if s.engine == "cycles" else None}


def run(ctx: JobContext) -> dict[str, Any]:
    import bpy

    from cartoon_engine.scene import files

    params = RenderSceneParams.model_validate(ctx.envelope.params)
    rs = ResolvedScene.model_validate(params.resolved)
    settings = RenderSettings.model_validate(params.settings)
    ws, out = ctx.workspace, ctx.out_dir
    blend = ws / "scene" / "scene.blend"
    if files.sha256(blend) != params.scene_blend_sha256:
        raise RuntimeError("scene/scene.blend is not the stored scene file")
    before = files.hash_sources(ws, rs)
    if before != files.expected_files(rs):
        raise RuntimeError("a staged file does not match its pin")
    ctx.progress(0.02, "opening scene.blend")
    blend_io.open_file(blend)
    scene = bpy.context.scene
    libs = files.library_check(blend.parent, ws, rs)
    missing = [lib.filepath for lib in bpy.data.libraries if lib.is_missing]
    if not libs["ok"] or missing:
        raise RuntimeError(f"scene.blend links outside its pins or misses a library: {missing or libs}")
    if scene.get("studio_manifest_digest") != rs.manifest_digest:
        raise RuntimeError("scene.blend was not built from this scene version's manifest")
    if settings.fps != rs.fps:
        raise RuntimeError(f"the render settings are {settings.fps} fps; the scene is {rs.fps} fps")
    in_force = apply_settings(scene, settings)
    f0 = params.frame_start if params.frame_start is not None else rs.frame_start
    f1 = params.frame_end if params.frame_end is not None else rs.frame_end
    if not rs.frame_start <= f0 <= f1 <= rs.frame_end:
        raise RuntimeError(f"frames {f0}..{f1} are outside the scene's {rs.frame_start}..{rs.frame_end}")
    fdir = out / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    frames, times = [], []
    total = f1 - f0 + 1
    for i, f in enumerate(range(f0, f1 + 1)):
        ctx.progress(0.05 + 0.9 * i / total, f"rendering frame {f} ({i + 1}/{total})")
        t = time.monotonic()
        scene.frame_set(f)
        path = fdir / f"frame_{f:04d}.png"
        scene.render.filepath = str(path)
        try:
            bpy.ops.render.render(write_still=True)
        except RuntimeError as exc:
            if settings.engine == "eevee":
                raise EngineUnavailable(f"Eevee could not render on this worker (it needs a GPU or EGL): {exc}") from exc
            raise
        if not path.is_file():
            if settings.engine == "eevee":
                raise EngineUnavailable("Eevee wrote no frame on this worker (it needs a GPU or EGL)")
            raise RuntimeError(f"frame {f} was not written")
        times.append(round(time.monotonic() - t, 3))
        frames.append({"frame": f, "path": f"frames/{path.name}",
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        ctx.output(path, "frame", "image/png")
    ok_src, changed = files.unchanged(ws, rs)
    if not ok_src:
        raise RuntimeError(f"source files changed while rendering: {changed}")
    doc = {"scene": rs.name, "manifest_digest": rs.manifest_digest, "scene_blend_sha256": params.scene_blend_sha256,
           "render_job_id": str(params.render_job_id), "settings": settings.model_dump(mode="json"),
           "in_force": in_force, "frame_start": f0, "frame_end": f1, "frames": frames,
           "seconds_per_frame": round(sum(times) / max(len(times), 1), 3), "seconds": round(sum(times), 2),
           "camera": scene.camera.name if scene.camera else None, "libraries": libs,
           "sources": {"before": before, "unchanged": ok_src}, "pins": sorted(f"{p.code}@{p.version}" for p in rs.pins()),
           "blender_version": blend_io.blender_version(), "engine_version": cartoon_engine.__version__}
    write_json(out / "render_report.json", doc)
    ctx.output(out / "render_report.json", "report", "application/json")
    ctx.progress(1.0, f"rendered {len(frames)} frames")
    return {"ok": True, "frames": len(frames), "frame_start": f0, "frame_end": f1,
            "seconds_per_frame": doc["seconds_per_frame"], "in_force": in_force, "report": doc}
