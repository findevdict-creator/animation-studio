"""``generate_character``: specification -> rigged character .blend, validation report,
turntable, expression sheet and browser GLB (SRS Phase 2).

The file is saved first, then reopened with scripts disabled and validated, and every
preview is made from the reopened file, so the outputs describe exactly what was saved.
A character that fails validation fails the job (its log carries the report).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from studio_contracts.character import CharacterSpecification, resolve_spec
from studio_contracts.jobs import GenerateCharacterParams
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1

import cartoon_engine
from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json
from cartoon_engine.core.device import choose


def _stamp(obj, rc, digest: str) -> None:
    obj["studio_character_name"] = rc.name
    obj["studio_template"] = rc.template
    obj["studio_style_profile"] = rc.style_profile
    obj["studio_rig_profile"] = rc.rig_profile
    obj["studio_fit_profile"] = rc.fit_profile
    obj["studio_spec_digest"] = digest
    obj["studio_height_m"] = rc.height_m


def run(ctx: JobContext) -> dict[str, Any]:
    import bpy

    from cartoon_engine.character import previews
    from cartoon_engine.character.build import build_geometry
    from cartoon_engine.character.rig import build_rig
    from cartoon_engine.character.validate import find_parts, validate

    timings: dict[str, float] = {}
    t0 = time.monotonic()

    def lap(name: str) -> None:
        nonlocal t0
        timings[name] = round(time.monotonic() - t0, 2)
        t0 = time.monotonic()

    params = GenerateCharacterParams.model_validate(ctx.envelope.params)
    spec = CharacterSpecification.model_validate(params.spec)
    rc = resolve_spec(spec)
    digest = rc.digest()
    opts = previews.RenderOptions(resolution=params.previews.resolution, samples=params.previews.samples,
                                  turntable_frames=params.previews.turntable_frames,
                                  sheet_tile=params.previews.sheet_tile)
    out, tmp = ctx.out_dir, ctx.workspace / "tmp"
    tmp.mkdir(exist_ok=True)

    ctx.progress(0.03, "building geometry")
    blend_io.reset_empty()
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.render.fps = opts.fps
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", rc.name).strip("_") or "character"
    ch = build_geometry(rc, collection_name=f"CHAR_{safe}")
    lap("geometry_s")
    ctx.progress(0.15, "rigging (Rigify)")
    rig_stats = build_rig(ch)
    for target in (ch.rig, ch.collection):
        _stamp(target, rc, digest)
    scene["studio_job_id"] = str(ctx.envelope.job_id)
    counts = {o.name: len(o.data.vertices) for o in ch.meshes()}
    lap("rig_s")

    blend = out / "character.blend"
    ctx.progress(0.25, "saving")
    blend_io.save(blend)
    blend_io.open_file(blend)
    parts = find_parts()
    lap("save_reopen_s")

    ctx.progress(0.3, "validating")
    report = validate(parts, rc.height_m, CARTOON_HUMANOID_V1)
    # the report names the exact file it describes, so a publisher can tell it belongs to the blend
    blend_sha = hashlib.sha256(blend.read_bytes()).hexdigest()
    report_doc = {"character": rc.name, "spec_digest": digest, "blend_sha256": blend_sha,
                  "rig_profile": CARTOON_HUMANOID_V1.ref,
                  "blender_version": blend_io.blender_version(), "engine_version": cartoon_engine.__version__,
                  **report.as_dict()}
    write_json(out / "validation.json", report_doc)
    lap("validate_s")
    if not report.ok:
        print("VALIDATION", json.dumps(report_doc, indent=1))
        raise RuntimeError(f"character failed validation: {report.failed()}")

    write_json(out / "character.json", {"specification": spec.model_dump(mode="json"),
                                        "resolved": rc.model_dump(mode="json"), "resolved_digest": digest})
    ctx.output(blend, "blend", "application/x-blender")
    ctx.output(out / "character.json", "spec", "application/json")
    ctx.output(out / "validation.json", "report", "application/json")

    data: dict[str, Any] = {}
    if params.previews.turntable:
        def tt_progress(i: int, n: int) -> None:
            ctx.progress(0.35 + 0.4 * i / n, f"turntable frame {i}/{n}")

        tt = previews.turntable(parts, tmp, opts, tt_progress)
        for src, name, role, mime in ((tt["thumbnail"], "thumbnail.png", "thumbnail", "image/png"),
                                      (tt["mp4"], "turntable.mp4", "turntable", "video/mp4"),
                                      (tt["sheet"], "turntable_sheet.png", "preview", "image/png")):
            dst = out / name
            Path(src).replace(dst)
            ctx.output(dst, role, mime)
        data["turntable_frames"] = tt["frames"]
        lap("turntable_s")
    if params.previews.expression_sheet:
        ctx.progress(0.78, "expression sheet")
        sheet = previews.expression_sheet(parts, tmp, opts, CARTOON_HUMANOID_V1.face_control_bone)
        dst = out / "expressions.png"
        Path(sheet["path"]).replace(dst)
        ctx.output(dst, "preview", "image/png")
        data["expression_sheet"] = sheet["tiles"]
        lap("expressions_s")
    if params.previews.glb:
        ctx.progress(0.92, "GLB export")
        glb = previews.export_glb(parts, out / "preview.glb")
        head = glb["meshes"].get("head", {})
        if head.get("morph_targets") != len(CARTOON_HUMANOID_V1.shape_keys()) or glb["non_def_joints"]:
            raise RuntimeError(f"GLB check failed: {head.get('morph_targets')} morph targets, "
                               f"non-DEF joints {glb['non_def_joints'][:5]}")
        ctx.output(out / "preview.glb", "glb", "model/gltf-binary")
        data["glb"] = {"bytes": glb["bytes"], "joints": glb["joints"], "head_morph_targets": head["morph_targets"]}
        lap("glb_s")

    ctx.progress(1.0, "done")
    return {
        "name": rc.name, "resolved_digest": digest, "template": rc.template, "style_profile": rc.style_profile,
        "rig_profile": rc.rig_profile, "fit_profile": rc.fit_profile, "height_m": rc.height_m,
        "validation": {"ok": report.ok, "checks": len(report.checks)}, "report": report_doc,
        "blend_sha256": blend_sha, "rig": rig_stats, "vertices": counts,
        "render": {"engine": "CYCLES", "device": choose().backend, "resolution": opts.resolution, "samples": opts.samples},
        "timings_s": timings, **data,
    }
