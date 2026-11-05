"""``generate_animation``: animation specification -> ``animation.blend`` plus a check report
and a preview strip (SRS 30 Phase 6).

The Action is made on a freshly built, rigged reference character of the reference fit
profile: baked from a generator, or retargeted from the staged BVH take
(``inputs/source.bvh``). It is described (what it keys, its frames, loop, stride and the
reference leg length: ``AnimationCompat``), stamped with the specification's digest and
written to ``animation.blend`` alone (one ``ANIM_`` Action, no rig, no character). The saved
file is then played on the check bodies exactly as a scene plays it and measured. A failed
check does not fail the job: the report says what failed, and a draft whose check failed
cannot be submitted.
"""

from __future__ import annotations

import time
from typing import Any

from studio_contracts.animation import AnimationSpecification, resolve_animation
from studio_contracts.jobs import GenerateAnimationParams

import cartoon_engine
from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json
from cartoon_engine.jobs import animation_common as AC
from cartoon_engine.jobs.outfit_common import sha256


def run(ctx: JobContext) -> dict[str, Any]:
    from cartoon_engine.animation.apply import animation_file_contents, file_is_alone

    timings: dict[str, float] = {}
    t0 = time.monotonic()

    def lap(name: str) -> None:
        nonlocal t0
        timings[name] = round(time.monotonic() - t0, 2)
        t0 = time.monotonic()

    params = GenerateAnimationParams.model_validate(ctx.envelope.params)
    spec = AnimationSpecification.model_validate(params.spec)
    ra = resolve_animation(spec)
    digest = ra.digest()
    out, ws = ctx.out_dir, ctx.workspace
    if ra.bvh is not None:
        staged = {i.path: i.sha256 for i in ctx.envelope.inputs}
        got = sha256(ws / "inputs" / "source.bvh")
        if staged.get("inputs/source.bvh") != ra.bvh.sha256 or got != ra.bvh.sha256:
            raise RuntimeError(f"inputs/source.bvh has SHA-256 {got}, the specification says {ra.bvh.sha256}")

    ctx.progress(0.05, f"building the reference body ({ra.reference_fit_profile})")
    blend_io.reset_empty()
    rc, parts = AC.reference_body(ra.reference_fit_profile, ra.style_profile)
    lap("reference_body_s")
    ctx.progress(0.2, "retargeting the take" if ra.bvh else f"baking the {ra.key}")
    action, build, speed = AC.make_action(ra, parts.rig, ws)
    compat = AC.describe(ra, parts.rig, action, int(build["frames"]), speed)
    AC.stamp(action, ra, compat, digest)
    blend = out / "animation.blend"
    AC.save_action(action, blend)
    contents = animation_file_contents(blend)
    alone = file_is_alone(contents)
    blend_sha = sha256(blend)
    lap("build_save_s")

    plays = []
    bodies = AC.check_bodies(ra)
    for i, fp in enumerate(bodies):
        ctx.progress(0.3 + 0.4 * i / len(bodies), f"playing it on the {fp} reference body")
        plays.append(AC.play_and_measure(blend, ra, compat, fp))
    check = AC.judge(ra, compat, plays, alone)
    lap("check_s")
    ctx.progress(0.75, "preview strip")
    strip, thumb, frames = AC.preview_strip(blend, ra, compat, out / "frames.png", params.previews.tile,
                                            params.previews.samples)
    ctx.output(strip, "preview", "image/png")
    ctx.output(thumb, "thumbnail", "image/png")
    lap("previews_s")
    doc = {"animation": ra.name, "spec_digest": digest, "animation_blend_sha256": blend_sha,
           "reference": {"fit_profile": ra.reference_fit_profile, "height_m": rc.height_m},
           "compat": compat.model_dump(mode="json"), "build": build, "file": contents, "preview_frames": frames,
           **check, "blender_version": blend_io.blender_version(), "engine_version": cartoon_engine.__version__,
           "timings_s": timings}
    write_json(out / "check_report.json", doc)
    ctx.output(out / "check_report.json", "report", "application/json")
    write_json(out / "animation.json", {"specification": spec.model_dump(mode="json"),
                                        "resolved": ra.model_dump(mode="json"), "resolved_digest": digest,
                                        "compat": compat.model_dump(mode="json")})
    ctx.output(blend, "blend", "application/x-blender")
    ctx.output(out / "animation.json", "spec", "application/json")
    ctx.progress(1.0, "done" if doc["ok"] else "check failed; see the report")
    return {"name": ra.name, "resolved_digest": digest, "rig_profile": ra.rig_profile, "category": ra.category,
            "compat": compat.model_dump(mode="json"), "blend_sha256": blend_sha,
            "check": {"ok": doc["ok"], "failed": doc["failed"], "checks": len(doc["checks"])}, "report": doc,
            "timings_s": timings}
