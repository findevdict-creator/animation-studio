"""``generate_accessory``: accessory specification -> ``accessory.blend`` plus an attach
report, a close-up, a full-body view and a GLB on the reference body (SRS 8).

The item is modelled around its attachment frame on the reference body of its reference fit
profile and written to ``accessory.blend`` alone (one ``ACC_`` collection, one object, no
character, no rig). The saved file is then linked onto a freshly built, rigged reference
character through the same code every scene uses, and that is what the attach check and the
previews describe. A failed check does not fail the job: the report says what failed, and a
draft whose check failed cannot be submitted.
"""

from __future__ import annotations

import time
from typing import Any

from studio_contracts.accessory import AccessorySpecification, resolve_accessory
from studio_contracts.jobs import GenerateAccessoryParams

from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json
from cartoon_engine.jobs.accessory_common import (
    accessory_file_contents,
    attach_and_check,
    close_up,
    file_is_alone,
    full_body,
    reference_wearer,
)
from cartoon_engine.jobs.outfit_common import sha256


def run(ctx: JobContext) -> dict[str, Any]:
    import bpy

    from cartoon_engine.accessory.build import build_accessory
    from cartoon_engine.character.previews import RenderOptions
    from cartoon_engine.outfit import previews as OP

    timings: dict[str, float] = {}
    t0 = time.monotonic()

    def lap(name: str) -> None:
        nonlocal t0
        timings[name] = round(time.monotonic() - t0, 2)
        t0 = time.monotonic()

    params = GenerateAccessoryParams.model_validate(ctx.envelope.params)
    spec = AccessorySpecification.model_validate(params.spec)
    ra = resolve_accessory(spec)
    digest = ra.digest()
    out = ctx.out_dir

    ctx.progress(0.05, f"building the reference body ({ra.reference_fit_profile})")
    blend_io.reset_empty()
    rc, ref, parts = reference_wearer(ra, rigged=False)
    lap("reference_body_s")
    ctx.progress(0.15, f"modelling the {ra.key}")
    coll, obj, frame, stats = build_accessory(ra, ref.skeleton, parts, digest)
    coll["studio_job_id"] = str(ctx.envelope.job_id)
    blend = out / "accessory.blend"
    bpy.data.libraries.write(str(blend), {coll}, fake_user=True)
    contents = accessory_file_contents(blend)
    if not file_is_alone(contents):
        raise RuntimeError(f"accessory.blend holds more than the accessory: {contents}")
    blend_sha = sha256(blend)
    lap("build_save_s")

    ctx.progress(0.3, "rigging the reference character")
    blend_io.reset_empty()
    rc, ref, parts = reference_wearer(ra)
    lap("reference_rig_s")
    ctx.progress(0.45, "attaching and checking in every pose")
    doc, att = attach_and_check(blend, parts, ref.skeleton, ra)
    lap("attach_check_s")
    opts = RenderOptions(resolution=params.previews.resolution, samples=params.previews.samples)
    ctx.progress(0.65, "close-up")
    close_up(parts, att.obj, out / "thumbnail.png", opts)
    ctx.output(out / "thumbnail.png", "thumbnail", "image/png")
    ctx.progress(0.75, "full-body view")
    full_body(parts, att.obj, out / "on_reference.png", opts)
    ctx.output(out / "on_reference.png", "preview", "image/png")
    if params.previews.glb:
        ctx.progress(0.85, "GLB")
        glb = OP.export_tryon_glb(parts, [att.obj], out / "preview.glb")
        if att.obj.name not in glb["meshes"] or glb["non_def_joints"]:
            raise RuntimeError(f"GLB check failed: meshes {sorted(glb['meshes'])}, non-DEF {glb['non_def_joints'][:5]}")
        ctx.output(out / "preview.glb", "glb", "model/gltf-binary")
        doc["glb"] = {"bytes": glb["bytes"], "meshes": sorted(glb["meshes"])}
    lap("previews_s")
    doc = {"accessory": ra.name, "spec_digest": digest, "accessory_blend_sha256": blend_sha,
           "wearer": {"kind": "reference", "fit_profile": ra.reference_fit_profile, "height_m": rc.height_m},
           "rig_profile": ra.rig_profile, "file": contents, "build": stats, **doc}
    write_json(out / "attach_report.json", doc)
    ctx.output(out / "attach_report.json", "report", "application/json")
    write_json(out / "accessory.json", {"specification": spec.model_dump(mode="json"),
                                        "resolved": ra.model_dump(mode="json"), "resolved_digest": digest})
    ctx.output(blend, "blend", "application/x-blender")
    ctx.output(out / "accessory.json", "spec", "application/json")
    ctx.progress(1.0, "done" if doc["ok"] else "attach check failed; see the report")
    return {"name": ra.name, "resolved_digest": digest, "style_profile": ra.style_profile, "rig_profile": ra.rig_profile,
            "item": ra.item, "attach_point": ra.attach_point, "build": stats, "blend_sha256": blend_sha,
            "check": {"ok": doc["ok"], "failed": doc["failed"], "checks": len(doc["checks"])}, "report": doc,
            "timings_s": timings}
