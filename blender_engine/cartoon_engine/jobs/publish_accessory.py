"""``publish_accessory`` (Blender side): re-check the exact ``accessory.blend`` being published.

The publisher hands over the draft's ``accessory.blend`` (checked against its recorded
SHA-256) and the version's specification. In a fresh process the file must hold the item
alone (one ACC_ collection with one object and mesh, no armature, no character), carry the
specification's digest, and pass the attach check again on a freshly rigged reference body.
Nothing is written but the report.
"""

from __future__ import annotations

from typing import Any

from studio_contracts.accessory import AccessorySpecification, resolve_accessory

from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json
from cartoon_engine.jobs.accessory_common import (
    accessory_file_contents,
    attach_and_check,
    file_is_alone,
    reference_wearer,
)
from cartoon_engine.jobs.outfit_common import sha256

STAMPS = ("studio_accessory_digest", "studio_accessory_name", "studio_rig_profile", "studio_style_profile",
          "studio_item", "studio_attach_point", "studio_placement")


def run(ctx: JobContext) -> dict[str, Any]:
    (blend_input,) = [i for i in ctx.envelope.inputs if i.path.endswith("accessory.blend")]
    blend = ctx.workspace / blend_input.path
    sha = sha256(blend)
    if sha != blend_input.sha256:
        raise RuntimeError(f"input accessory.blend has SHA-256 {sha}, expected {blend_input.sha256}")
    spec = AccessorySpecification.model_validate(ctx.envelope.params["spec"])
    ra = resolve_accessory(spec)
    digest = ra.digest()

    ctx.progress(0.05, "reading accessory.blend")
    contents = accessory_file_contents(blend)
    alone = file_is_alone(contents)
    ctx.progress(0.15, f"building the reference character ({ra.reference_fit_profile})")
    blend_io.reset_empty()
    rc, ref, parts = reference_wearer(ra)
    ctx.progress(0.4, "attaching and checking")
    doc, att = attach_and_check(blend, parts, ref.skeleton, ra)
    stamps = {k: att.obj.data[k] for k in att.obj.data.keys() if k.startswith("studio_")}
    lib = att.obj.data.library
    coll_stamps = {}
    import bpy

    with bpy.data.libraries.load(str(blend), link=True) as (src, dst):
        dst.collections = list(src.collections)
    for c in dst.collections:
        if c is not None:
            coll_stamps = {k: c[k] for k in c.keys() if k.startswith("studio_")}
    checks = [
        {"name": "accessory_file_alone", "ok": alone, "detail": contents},
        {"name": "built_from_spec", "ok": coll_stamps.get("studio_accessory_digest") == digest
         and stamps.get("studio_accessory_digest") == digest,
         "detail": {"stamped": coll_stamps.get("studio_accessory_digest"), "spec": digest}},
        {"name": "rig_profile", "ok": coll_stamps.get("studio_rig_profile") == ra.rig_profile,
         "detail": coll_stamps.get("studio_rig_profile")},
        {"name": "attach_point", "ok": stamps.get("studio_attach_point") == ra.attach_point,
         "detail": stamps.get("studio_attach_point")},
        {"name": "mesh_linked_not_copied", "ok": lib is not None, "detail": lib.filepath if lib else None},
    ]
    ok = doc["ok"] and all(c["ok"] for c in checks)
    report = {"accessory": ra.name, "spec_digest": digest, "accessory_blend_sha256": sha, "purpose": "publish",
              "wearer": {"kind": "reference", "fit_profile": ra.reference_fit_profile, "height_m": rc.height_m},
              **doc, "ok": ok, "failed": [c["name"] for c in checks if not c["ok"]] + doc["failed"],
              "file_checks": checks}
    write_json(ctx.out_dir / "publish_validation.json", report)
    ctx.progress(1.0, "validated" if ok else "validation failed")
    return {"report": report, "stamps": {k: str(v) if not isinstance(v, (str, float, int)) else v
                                         for k, v in coll_stamps.items() if k in STAMPS}}
