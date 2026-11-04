"""``generate_outfit``: outfit specification -> ``outfit.blend`` plus a fit report, pose
sheet, thumbnail and try-on GLB on the reference body (SRS Phase 4).

The pieces are built on the reference body of the outfit's primary fit profile and written
to ``outfit.blend`` alone (no character, no rig: weights name the rig profile's deform
bones). The saved file is then attached back onto a freshly built reference character
through the same code every wearer uses, and that is what the fit check and the previews
describe. A failed fit check does not fail the job: the report and pose sheet say what
failed, and a draft whose fit failed cannot be submitted.
"""

from __future__ import annotations

import time
from typing import Any

from studio_contracts.character import resolve_spec
from studio_contracts.jobs import GenerateOutfitParams
from studio_contracts.outfit import (
    OutfitSpecification,
    reference_character,
    resolve_outfit,
)

from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json
from cartoon_engine.jobs.outfit_common import (
    fit_and_check,
    outfit_file_contents,
    sha256,
    write_report,
)


def run(ctx: JobContext) -> dict[str, Any]:
    import bpy

    from cartoon_engine.character.build import build_geometry
    from cartoon_engine.character.rig import build_rig
    from cartoon_engine.character.validate import find_parts
    from cartoon_engine.outfit.build import build_outfit

    timings: dict[str, float] = {}
    t0 = time.monotonic()

    def lap(name: str) -> None:
        nonlocal t0
        timings[name] = round(time.monotonic() - t0, 2)
        t0 = time.monotonic()

    params = GenerateOutfitParams.model_validate(ctx.envelope.params)
    spec = OutfitSpecification.model_validate(params.spec)
    ro = resolve_outfit(spec)
    digest = ro.digest()
    out = ctx.out_dir
    ref_spec = reference_character(ro.primary_fit_profile, ro.style_profile)
    ref_rc = resolve_spec(ref_spec)

    ctx.progress(0.05, f"building the reference body ({ro.primary_fit_profile})")
    blend_io.reset_empty()
    ref = build_geometry(ref_rc, collection_name=f"CHAR_REF_{ro.primary_fit_profile}")
    lap("reference_body_s")
    ctx.progress(0.12, "building garments")
    coll, stats = build_outfit(ro, ref, digest)
    coll["studio_job_id"] = str(ctx.envelope.job_id)
    lap("garments_s")
    blend = out / "outfit.blend"
    bpy.data.libraries.write(str(blend), {coll}, fake_user=True)
    contents = outfit_file_contents(blend)
    if contents["armatures"] or len(contents["collections"]) != 1 or not contents["collections"][0].startswith("OUTFIT_"):
        raise RuntimeError(f"outfit.blend holds more than the outfit: {contents}")
    blend_sha = sha256(blend)
    lap("save_s")

    # the check describes the saved file: a fresh reference character, the file appended to it
    ctx.progress(0.2, "rigging the reference character")
    blend_io.reset_empty()
    ref = build_geometry(ref_rc, collection_name=f"CHAR_REF_{ro.primary_fit_profile}")
    build_rig(ref)
    parts = find_parts(ref.collection)
    lap("reference_rig_s")
    doc, _ = fit_and_check(ctx, blend, parts, ref.skeleton, ro.style_profile, params.previews, 0.35)
    lap("fit_check_previews_s")
    doc = {"outfit": ro.name, "spec_digest": digest, "outfit_blend_sha256": blend_sha,
           "wearer": {"kind": "reference", "fit_profile": ro.primary_fit_profile, "height_m": ref_rc.height_m},
           "rig_profile": ro.rig_profile, "file": contents, **doc}
    write_report(ctx, doc)
    write_json(out / "outfit.json", {"specification": spec.model_dump(mode="json"),
                                     "resolved": ro.model_dump(mode="json"), "resolved_digest": digest})
    ctx.output(blend, "blend", "application/x-blender")
    ctx.output(out / "outfit.json", "spec", "application/json")
    ctx.progress(1.0, "done" if doc["ok"] else "fit check failed; see the report")
    return {"name": ro.name, "resolved_digest": digest, "style_profile": ro.style_profile, "rig_profile": ro.rig_profile,
            "fit_profiles": list(ro.fit_profiles), "primary_fit_profile": ro.primary_fit_profile,
            "pieces": [p.object_name for p in ro.pieces], "build": stats, "blend_sha256": blend_sha,
            "fit": {"ok": doc["ok"], "failed": doc["failed"], "checks": len(doc["checks"])}, "report": doc,
            "timings_s": timings}
