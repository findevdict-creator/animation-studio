"""Shared by the outfit jobs: fit, check, previews and the report document."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from studio_contracts.jobs import OutfitPreviewSettings
from studio_contracts.outfit import get_conventions

import cartoon_engine
from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def outfit_file_contents(path: Path) -> dict[str, Any]:
    """What an outfit file holds, read without loading it: one OUTFIT_ collection, meshes
    and materials, no armature, no character."""
    import bpy

    with bpy.data.libraries.load(str(path), link=True) as (src, _):
        return {"collections": list(src.collections), "objects": list(src.objects),
                "armatures": list(src.armatures), "meshes": len(src.meshes), "materials": len(src.materials)}


def fit_and_check(ctx: JobContext, outfit_blend: Path, parts, target_sk, style_ref: str,
                  previews: OutfitPreviewSettings, progress0: float = 0.4) -> tuple[dict[str, Any], Any]:
    """Attach ``outfit_blend`` to ``parts``, run the fit check, write the previews. Returns
    (report document without identity fields, fitted outfit)."""
    from cartoon_engine.character.previews import RenderOptions
    from cartoon_engine.outfit import previews as P
    from cartoon_engine.outfit.check import fit_check
    from cartoon_engine.outfit.fit import attach_outfit

    conv = get_conventions(style_ref)
    ctx.progress(progress0, "fitting the outfit")
    fitted = attach_outfit(outfit_blend, parts, target_sk, min_clearance=conv.min_clearance_m)
    ctx.progress(progress0 + 0.1, "fit check: posing")
    report, per_pose = fit_check(parts, fitted.garments, conv.fit, conv.poses)
    doc = {"ok": report.ok, "failed": report.failed(), "checks": report.checks, "poses": per_pose,
           "fit": fitted.as_dict(), "thresholds": conv.fit.model_dump(), "pose_suite": list(conv.poses),
           "blender_version": blend_io.blender_version(), "engine_version": cartoon_engine.__version__}

    out, tmp = ctx.out_dir, ctx.workspace / "tmp"
    tmp.mkdir(exist_ok=True)
    opts = RenderOptions(resolution=previews.resolution, samples=previews.samples)
    ctx.progress(progress0 + 0.2, "thumbnail")
    P.thumbnail(parts, fitted.garments, out / "thumbnail.png", opts)
    ctx.output(out / "thumbnail.png", "thumbnail", "image/png")
    if previews.pose_sheet:
        ctx.progress(progress0 + 0.3, "pose sheet")
        P.pose_sheet(parts, fitted.garments, out / "poses.png", opts, conv.poses, tile=previews.pose_tile)
        ctx.output(out / "poses.png", "preview", "image/png")
    if previews.glb:
        ctx.progress(progress0 + 0.5, "try-on GLB")
        glb = P.export_tryon_glb(parts, fitted.garments, out / "preview.glb")
        missing = [g.name for g in fitted.garments if g.name not in glb["meshes"]]
        if missing or glb["non_def_joints"]:
            raise RuntimeError(f"GLB check failed: missing {missing}, non-DEF joints {glb['non_def_joints'][:5]}")
        ctx.output(out / "preview.glb", "glb", "model/gltf-binary")
        doc["glb"] = {"bytes": glb["bytes"], "meshes": sorted(glb["meshes"])}
    return doc, fitted


def write_report(ctx: JobContext, doc: dict[str, Any]) -> Path:
    path = ctx.out_dir / "fit_report.json"
    write_json(path, doc)
    ctx.output(path, "report", "application/json")
    return path
