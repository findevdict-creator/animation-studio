"""``publish_outfit`` (Blender side): re-check the exact ``outfit.blend`` being published.

The publisher hands over the draft's ``outfit.blend`` (checked against its recorded SHA-256)
and the version's specification. Here the file is checked again from scratch, in a fresh
process: it must hold the outfit alone (one OUTFIT_ collection, no armature, no character),
be stamped with the specification's digest, and pass the fit check again on a freshly built
reference body of its primary fit profile. Nothing is written but the report.
"""

from __future__ import annotations

from typing import Any

from studio_contracts.character import resolve_spec
from studio_contracts.outfit import OutfitSpecification, get_conventions, reference_character, resolve_outfit

import cartoon_engine
from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json
from cartoon_engine.jobs.outfit_common import outfit_file_contents, sha256


def run(ctx: JobContext) -> dict[str, Any]:
    from cartoon_engine.character.build import build_geometry
    from cartoon_engine.character.rig import build_rig
    from cartoon_engine.character.validate import find_parts
    from cartoon_engine.outfit.check import fit_check
    from cartoon_engine.outfit.fit import attach_outfit

    (blend_input,) = [i for i in ctx.envelope.inputs if i.path.endswith("outfit.blend")]
    blend = ctx.workspace / blend_input.path
    sha = sha256(blend)
    if sha != blend_input.sha256:
        raise RuntimeError(f"input outfit.blend has SHA-256 {sha}, expected {blend_input.sha256}")
    spec = OutfitSpecification.model_validate(ctx.envelope.params["spec"])
    ro = resolve_outfit(spec)
    digest = ro.digest()

    ctx.progress(0.05, "reading outfit.blend")
    contents = outfit_file_contents(blend)
    alone = (not contents["armatures"] and len(contents["collections"]) == 1
             and contents["collections"][0].startswith("OUTFIT_"))

    ctx.progress(0.15, f"building the reference character ({ro.primary_fit_profile})")
    blend_io.reset_empty()
    ref_rc = resolve_spec(reference_character(ro.primary_fit_profile, ro.style_profile))
    ref = build_geometry(ref_rc, collection_name=f"CHAR_REF_{ro.primary_fit_profile}")
    build_rig(ref)
    parts = find_parts(ref.collection)
    conv = get_conventions(ro.style_profile)
    ctx.progress(0.35, "fitting and checking")
    fitted = attach_outfit(blend, parts, ref.skeleton, min_clearance=conv.min_clearance_m)
    stamps = {k: fitted.collection[k] for k in fitted.collection.keys() if k.startswith("studio_")}
    report, _ = fit_check(parts, fitted.garments, conv.fit, conv.poses)
    checks = [
        {"name": "outfit_file_alone", "ok": alone, "detail": contents},
        {"name": "built_from_spec", "ok": stamps.get("studio_outfit_digest") == digest,
         "detail": {"stamped": stamps.get("studio_outfit_digest"), "spec": digest}},
        {"name": "rig_profile", "ok": stamps.get("studio_rig_profile") == ro.rig_profile,
         "detail": stamps.get("studio_rig_profile")},
    ]
    ok = report.ok and all(c["ok"] for c in checks)
    doc = {"outfit": ro.name, "spec_digest": digest, "outfit_blend_sha256": sha, "purpose": "publish",
           "wearer": {"kind": "reference", "fit_profile": ro.primary_fit_profile, "height_m": ref_rc.height_m},
           "ok": ok, "failed": [c["name"] for c in checks if not c["ok"]] + report.failed(),
           "file_checks": checks, "checks": report.checks, "thresholds": conv.fit.model_dump(),
           "pose_suite": list(conv.poses), "blender_version": blend_io.blender_version(),
           "engine_version": cartoon_engine.__version__}
    write_json(ctx.out_dir / "publish_validation.json", doc)
    ctx.progress(1.0, "validated" if ok else "validation failed")
    return {"report": doc, "stamps": {k: v if isinstance(v, (str, float, int)) else str(v) for k, v in stamps.items()
                                      if k in ("studio_outfit_digest", "studio_outfit_name", "studio_rig_profile",
                                               "studio_style_profile", "studio_fit_profile", "studio_scope")}}
