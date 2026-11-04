"""``fit_outfit``: put one outfit version on one character version and run the fit check
(the compatibility registry's evidence, and the try-on preview of the web app).

Inputs, materialised by the worker from the two versions' files and checked against their
SHA-256: ``character.blend``, ``character.json`` (the specification it was built from, so
the character's skeleton can be rebuilt exactly) and ``outfit.blend``. The character file is
opened with scripts disabled; the outfit is appended to it and fitted.
"""

from __future__ import annotations

import json
from typing import Any

from studio_contracts.character import ResolvedCharacter
from studio_contracts.jobs import FitOutfitParams

from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext
from cartoon_engine.jobs.outfit_common import (
    fit_and_check,
    outfit_file_contents,
    sha256,
    write_report,
)

MAX_RIG_MISMATCH_M = 0.002


def _input(ctx: JobContext, name: str):
    match = [i for i in ctx.envelope.inputs if i.path.endswith(name)]
    if len(match) != 1:
        raise RuntimeError(f"expected one input {name}, got {[i.path for i in ctx.envelope.inputs]}")
    path = ctx.workspace / match[0].path
    if sha256(path) != match[0].sha256:
        raise RuntimeError(f"input {name} does not match its SHA-256")
    return path


def run(ctx: JobContext) -> dict[str, Any]:
    import bpy

    from cartoon_engine.character.skeleton import build_skeleton
    from cartoon_engine.character.validate import find_parts
    from cartoon_engine.outfit.fit import rig_skeleton_mismatch

    params = FitOutfitParams.model_validate(ctx.envelope.params)
    char_blend, char_json, outfit_blend = (_input(ctx, n) for n in ("character.blend", "character.json", "outfit.blend"))
    rc = ResolvedCharacter.model_validate(json.loads(char_json.read_text())["resolved"])
    contents = outfit_file_contents(outfit_blend)

    ctx.progress(0.05, "opening character.blend (scripts disabled)")
    blend_io.open_file(char_blend)
    parts = find_parts()
    if parts.rig.get("studio_rig_profile") != rc.rig_profile:
        raise RuntimeError(f"rig profile {parts.rig.get('studio_rig_profile')!r} is not {rc.rig_profile}")
    sk = build_skeleton(rc)
    mismatch = rig_skeleton_mismatch(parts, sk)
    if mismatch > MAX_RIG_MISMATCH_M:
        raise RuntimeError(f"character.json does not describe this rig (joints {mismatch * 1000:.1f} mm apart)")
    doc, fitted = fit_and_check(ctx, outfit_blend, parts, sk, rc.style_profile, params.previews, 0.2)
    coll = fitted.collection
    declared = list(coll.get("studio_fit_profiles", []))
    doc = {"outfit": coll.get("studio_outfit_name"), "spec_digest": coll.get("studio_outfit_digest"),
           "outfit_blend_sha256": sha256(outfit_blend), "outfit_version_id": str(params.outfit_version_id),
           "wearer": {"kind": "character", "character_version_id": str(params.character_version_id),
                      "name": rc.name, "fit_profile": rc.fit_profile, "height_m": rc.height_m,
                      "character_blend_sha256": sha256(char_blend), "rig_mismatch_m": round(mismatch, 5)},
           "declared_fit_profiles": declared, "fit_profile_declared": rc.fit_profile in declared,
           "rig_profile": rc.rig_profile, "file": contents, **doc}
    write_report(ctx, doc)
    ctx.progress(1.0, "fits" if doc["ok"] else "fit check failed")
    del bpy
    return {"ok": doc["ok"], "failed": doc["failed"], "fit_profile_declared": doc["fit_profile_declared"],
            "report": doc}
