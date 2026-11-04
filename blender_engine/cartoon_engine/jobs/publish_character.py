"""``publish_character`` (Blender side): re-validate the exact file being published.

The publisher downloads ``character.blend`` from the draft, checks it against the SHA-256
recorded for the draft, and hands it to this process as the job's only input. Here the file
is checked again, opened with scripts disabled and validated from scratch, so publication
never rests on a report written by an earlier process. Nothing is written but the report;
copying to the library is the publisher's job, outside Blender.
"""

from __future__ import annotations

import hashlib
from typing import Any

from studio_contracts.rig_profile import CARTOON_HUMANOID_V1

import cartoon_engine
from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json


def run(ctx: JobContext) -> dict[str, Any]:
    from cartoon_engine.character.validate import find_parts, validate

    (blend_input,) = [i for i in ctx.envelope.inputs if i.path.endswith("character.blend")]
    blend = ctx.workspace / blend_input.path
    sha = hashlib.sha256(blend.read_bytes()).hexdigest()
    if sha != blend_input.sha256:
        raise RuntimeError(f"input character.blend has SHA-256 {sha}, expected {blend_input.sha256}")

    ctx.progress(0.1, "opening character.blend (scripts disabled)")
    blend_io.open_file(blend)
    parts = find_parts()
    stamps = {k: parts.rig[k] for k in parts.rig.keys() if k.startswith("studio_")}
    if stamps.get("studio_rig_profile") != CARTOON_HUMANOID_V1.ref:
        raise RuntimeError(f"rig profile {stamps.get('studio_rig_profile')!r} is not {CARTOON_HUMANOID_V1.ref}")

    ctx.progress(0.3, "validating")
    report = validate(parts, float(stamps["studio_height_m"]), CARTOON_HUMANOID_V1)
    doc = {"character": stamps.get("studio_character_name"), "spec_digest": stamps.get("studio_spec_digest"),
           "blend_sha256": sha, "rig_profile": CARTOON_HUMANOID_V1.ref,
           "blender_version": blend_io.blender_version(), "engine_version": cartoon_engine.__version__,
           "purpose": "publish", **report.as_dict()}
    write_json(ctx.out_dir / "publish_validation.json", doc)
    ctx.progress(1.0, "validated" if report.ok else "validation failed")
    return {"report": doc, "stamps": {k: (float(v) if isinstance(v, float) else str(v)) for k, v in stamps.items()}}
