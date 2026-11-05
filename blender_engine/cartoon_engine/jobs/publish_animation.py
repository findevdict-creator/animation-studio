"""``publish_animation`` (Blender side): re-check the exact ``animation.blend`` being published.

The publisher hands over the draft's ``animation.blend`` (checked against its recorded
SHA-256) and the version's specification. In a fresh process the file must hold the Action
alone, carry the specification's digest and a compatibility record equal to the one stored
with the draft, and pass the play check again on freshly rigged reference bodies. Nothing
is written but the report.
"""

from __future__ import annotations

from typing import Any

from studio_contracts.animation import (
    AnimationCompat,
    AnimationSpecification,
    resolve_animation,
)

import cartoon_engine
from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json
from cartoon_engine.jobs import animation_common as AC
from cartoon_engine.jobs.outfit_common import sha256


def run(ctx: JobContext) -> dict[str, Any]:
    from cartoon_engine.animation.apply import animation_file_contents, file_is_alone

    (blend_input,) = [i for i in ctx.envelope.inputs if i.path.endswith("animation.blend")]
    blend = ctx.workspace / blend_input.path
    sha = sha256(blend)
    if sha != blend_input.sha256:
        raise RuntimeError(f"input animation.blend has SHA-256 {sha}, expected {blend_input.sha256}")
    spec = AnimationSpecification.model_validate(ctx.envelope.params["spec"])
    expected = AnimationCompat.model_validate(ctx.envelope.params["compat"])
    ra = resolve_animation(spec)
    digest = ra.digest()
    ctx.progress(0.05, "reading animation.blend")
    contents = animation_file_contents(blend)
    compat, stamps = AC.compat_from_file(blend)
    plays = []
    if compat is not None:
        for i, fp in enumerate(AC.check_bodies(ra)):
            ctx.progress(0.1 + 0.8 * i / 3, f"playing it on the {fp} reference body")
            plays.append(AC.play_and_measure(blend, ra, compat, fp))
    check = AC.judge(ra, compat or expected, plays, file_is_alone(contents))
    file_checks = [
        {"name": "built_from_spec", "ok": stamps.get("studio_animation_digest") == digest,
         "detail": {"stamped": stamps.get("studio_animation_digest"), "spec": digest}},
        {"name": "rig_profile", "ok": stamps.get("studio_rig_profile") == ra.rig_profile,
         "detail": stamps.get("studio_rig_profile")},
        {"name": "compat_matches_draft", "ok": compat == expected,
         "detail": compat.model_dump(mode="json") if compat else None},
    ]
    ok = check["ok"] and bool(plays) and all(c["ok"] for c in file_checks)
    report = {"animation": ra.name, "spec_digest": digest, "animation_blend_sha256": sha, "purpose": "publish",
              **check, "ok": ok, "failed": [c["name"] for c in file_checks if not c["ok"]] + check["failed"],
              "file_checks": file_checks, "blender_version": blend_io.blender_version(),
              "engine_version": cartoon_engine.__version__}
    write_json(ctx.out_dir / "publish_validation.json", report)
    ctx.progress(1.0, "validated" if ok else "validation failed")
    return {"report": report, "stamps": {k: v for k, v in stamps.items() if isinstance(v, (str, int, float))}}
