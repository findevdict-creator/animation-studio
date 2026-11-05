"""``generate_prop``: prop specification -> ``prop.blend``, a measured report, a thumbnail and
a GLB (SRS 12, Phase 7).

The prop is built alone into ``prop.blend`` (one ``PROP_`` collection, one object). The saved
file is then linked into a fresh session, the way scenes will use it, and that linked object
is what is measured and rendered. A failed check does not fail the job; the report says what
failed and a draft whose check failed cannot be submitted.
"""

from __future__ import annotations

from typing import Any

from studio_contracts.environment import PropSpecification, resolve_prop
from studio_contracts.jobs import GeneratePropParams

import cartoon_engine
from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json
from cartoon_engine.jobs.outfit_common import sha256


def run(ctx: JobContext) -> dict[str, Any]:
    import bpy

    from cartoon_engine.character.previews import RenderOptions
    from cartoon_engine.environment import previews
    from cartoon_engine.environment.build import build_prop_file
    from cartoon_engine.environment.check import check_prop
    from cartoon_engine.environment.library import (
        file_contents,
        link_collections,
        prop_file_alone,
        stamps,
    )

    params = GeneratePropParams.model_validate(ctx.envelope.params)
    spec = PropSpecification.model_validate(params.spec)
    rp = resolve_prop(spec)
    digest = rp.digest()
    out = ctx.out_dir

    ctx.progress(0.1, f"modelling the {rp.key}")
    blend_io.reset_empty()
    coll, _ = build_prop_file(rp, digest)
    coll["studio_job_id"] = str(ctx.envelope.job_id)
    blend = out / "prop.blend"
    bpy.data.libraries.write(str(blend), {coll}, fake_user=True)
    contents = file_contents(blend)
    if not prop_file_alone(contents):
        raise RuntimeError(f"prop.blend holds more than the prop: {contents}")
    blend_sha = sha256(blend)

    ctx.progress(0.3, "linking and measuring")
    blend_io.reset_empty()
    linked = link_collections(blend, ("PROP_",))["PROP_"]
    (obj,) = linked.objects
    checks = check_prop(obj, rp)
    stamped = stamps(linked)
    checks.append({"name": "built_from_spec", "ok": stamped.get("studio_prop_digest") == digest,
                   "detail": {"stamped": stamped.get("studio_prop_digest"), "spec": digest}})
    checks.append({"name": "linked_not_copied", "ok": obj.library is not None,
                   "detail": obj.library.filepath if obj.library else None})
    failed = [c["name"] for c in checks if not c["ok"]]

    ctx.progress(0.5, "thumbnail")
    opts = RenderOptions(resolution=params.previews.resolution, samples=params.previews.samples)
    previews.prop_thumbnail(obj, out / "thumbnail.png", opts)
    ctx.output(out / "thumbnail.png", "thumbnail", "image/png")
    doc: dict[str, Any] = {"prop": rp.name, "spec_digest": digest, "prop_blend_sha256": blend_sha, "item": rp.item,
                           "size": list(rp.size), "file": contents, "ok": not failed, "failed": failed,
                           "checks": checks, "blender_version": blend_io.blender_version(),
                           "engine_version": cartoon_engine.__version__}
    if params.previews.glb:
        ctx.progress(0.8, "GLB")
        glb = previews.export_glb(linked, out / "preview.glb")
        ctx.output(out / "preview.glb", "glb", "model/gltf-binary")
        doc["glb"] = {"bytes": glb["bytes"], "meshes": sorted(glb["meshes"])}
    write_json(out / "prop_report.json", doc)
    ctx.output(out / "prop_report.json", "report", "application/json")
    write_json(out / "prop.json", {"specification": spec.model_dump(mode="json"), "resolved": rp.model_dump(mode="json"),
                                   "resolved_digest": digest})
    ctx.output(blend, "blend", "application/x-blender")
    ctx.output(out / "prop.json", "spec", "application/json")
    ctx.progress(1.0, "done" if not failed else "prop check failed; see the report")
    return {"name": rp.name, "resolved_digest": digest, "style_profile": rp.style_profile, "item": rp.item,
            "size": list(rp.size), "blend_sha256": blend_sha,
            "check": {"ok": not failed, "failed": failed, "checks": len(checks)}, "report": doc}
