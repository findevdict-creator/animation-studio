"""``generate_environment``: environment specification -> ``environment.blend``, a layout
report measured in Blender, a wide render, a top plan and a GLB (SRS 12, Phase 7).

The set and its lighting rig are built and written alone to ``environment.blend``
(``ENV_<key>`` and ``LIGHTS_<key>``). The saved file is linked into a fresh session, as a
scene will link it, and the linked set is what is measured (``environment.check``) and
rendered. A failed check does not fail the job; the report says what failed and a draft
whose check failed cannot be submitted.
"""

from __future__ import annotations

import time
from typing import Any

from studio_contracts.environment import EnvironmentSpecification, resolve_environment
from studio_contracts.jobs import GenerateEnvironmentParams

import cartoon_engine
from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json
from cartoon_engine.jobs.outfit_common import sha256


def run(ctx: JobContext) -> dict[str, Any]:
    import bpy

    from cartoon_engine.environment import previews
    from cartoon_engine.environment.build import build_environment_file
    from cartoon_engine.environment.check import check_environment
    from cartoon_engine.environment.library import (
        environment_file_alone,
        file_contents,
        link_collections,
        stamps,
    )

    timings: dict[str, float] = {}
    t0 = time.monotonic()

    def lap(name: str) -> None:
        nonlocal t0
        timings[name] = round(time.monotonic() - t0, 2)
        t0 = time.monotonic()

    params = GenerateEnvironmentParams.model_validate(ctx.envelope.params)
    spec = EnvironmentSpecification.model_validate(params.spec)
    env = resolve_environment(spec)
    digest = env.digest()
    out = ctx.out_dir

    ctx.progress(0.05, f"building the {env.key} set")
    blend_io.reset_empty()
    coll, lights, info = build_environment_file(env, digest)
    coll["studio_job_id"] = str(ctx.envelope.job_id)
    blend = out / "environment.blend"
    bpy.data.libraries.write(str(blend), {coll, lights}, fake_user=True)
    contents = file_contents(blend)
    if not environment_file_alone(contents):
        raise RuntimeError(f"environment.blend holds more than the set and its lights: {contents}")
    blend_sha = sha256(blend)
    lap("build_save_s")

    ctx.progress(0.2, "linking and measuring the layout")
    blend_io.reset_empty()
    linked = link_collections(blend, ("ENV_", "LIGHTS_"))
    report = check_environment(linked["ENV_"], linked["LIGHTS_"], env)
    stamped = stamps(linked["ENV_"])
    extra = [{"name": "built_from_spec", "ok": stamped.get("studio_environment_digest") == digest,
              "detail": {"stamped": stamped.get("studio_environment_digest"), "spec": digest}},
             {"name": "linked_not_copied", "ok": linked["ENV_"].library is not None,
              "detail": linked["ENV_"].library.filepath if linked["ENV_"].library else None}]
    report["checks"] += extra
    report["failed"] += [c["name"] for c in extra if not c["ok"]]
    report["ok"] = not report["failed"]
    lap("check_s")

    res, samples = params.previews.resolution, params.previews.samples
    ctx.progress(0.4, "wide render")
    previews.wide(env, out / "wide.png", res, samples)
    ctx.output(out / "wide.png", "preview", "image/png")
    ctx.progress(0.65, "top plan")
    previews.plan(env, out / "plan.png", res, samples)
    ctx.output(out / "plan.png", "preview", "image/png")
    # the thumbnail is the wide render at gallery size
    previews.wide(env, out / "thumbnail.png", max(64, res // 2), max(1, samples // 2))
    ctx.output(out / "thumbnail.png", "thumbnail", "image/png")
    lap("previews_s")
    doc: dict[str, Any] = {"environment": env.name, "spec_digest": digest, "environment_blend_sha256": blend_sha,
                           "template": env.template, "size": list(env.size), "lighting": env.lighting.key,
                           "file": contents, "build": info, **report, "layout": env.layout(),
                           "blender_version": blend_io.blender_version(), "engine_version": cartoon_engine.__version__}
    if params.previews.glb:
        ctx.progress(0.85, "GLB")
        glb = previews.export_glb(linked["ENV_"], out / "preview.glb")
        ctx.output(out / "preview.glb", "glb", "model/gltf-binary")
        doc["glb"] = {"bytes": glb["bytes"], "meshes": len(glb["meshes"])}
        lap("glb_s")
    write_json(out / "layout_report.json", doc)
    ctx.output(out / "layout_report.json", "report", "application/json")
    write_json(out / "environment.json", {"specification": spec.model_dump(mode="json"),
                                          "resolved": env.model_dump(mode="json"), "resolved_digest": digest,
                                          "layout": env.layout()})
    ctx.output(blend, "blend", "application/x-blender")
    ctx.output(out / "environment.json", "spec", "application/json")
    ctx.progress(1.0, "done" if report["ok"] else "layout check failed; see the report")
    return {"name": env.name, "resolved_digest": digest, "style_profile": env.style_profile, "template": env.template,
            "size": list(env.size), "lighting": env.lighting.key, "layout": env.layout(), "blend_sha256": blend_sha,
            "check": {"ok": report["ok"], "failed": report["failed"], "checks": len(report["checks"])},
            "report": doc, "timings_s": timings}
