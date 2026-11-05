"""``publish_prop`` and ``publish_environment`` (Blender side): re-check the exact file being
published, in a fresh process.

The publisher stages the draft's ``prop.blend`` / ``environment.blend`` (checked against its
recorded SHA-256) and hands over the version's specification. The file must hold the asset
alone, carry the specification's digest, and pass the same measured checks again when
linked. Nothing is written but the report.
"""

from __future__ import annotations

from typing import Any

from studio_contracts.environment import (
    EnvironmentSpecification,
    PropSpecification,
    resolve_environment,
    resolve_prop,
)

import cartoon_engine
from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json
from cartoon_engine.jobs.outfit_common import sha256


def _input(ctx: JobContext, name: str):
    (inp,) = [i for i in ctx.envelope.inputs if i.path.endswith(name)]
    path = ctx.workspace / inp.path
    sha = sha256(path)
    if sha != inp.sha256:
        raise RuntimeError(f"input {name} has SHA-256 {sha}, expected {inp.sha256}")
    return path, sha


def _finish(ctx: JobContext, kind: str, name: str, digest: str, sha: str, report: dict, stamped: dict,
            contents: dict, alone: bool, linked) -> dict[str, Any]:
    file_checks = [
        {"name": f"{kind}_file_alone", "ok": alone, "detail": contents},
        {"name": "built_from_spec", "ok": stamped.get(f"studio_{kind}_digest") == digest,
         "detail": {"stamped": stamped.get(f"studio_{kind}_digest"), "spec": digest}},
        {"name": "linked_not_copied", "ok": linked.library is not None,
         "detail": linked.library.filepath if linked.library else None},
    ]
    failed = [c["name"] for c in file_checks if not c["ok"]] + report["failed"]
    doc = {kind: name, "spec_digest": digest, f"{kind}_blend_sha256": sha, "purpose": "publish", **report,
           "ok": not failed, "failed": failed, "file_checks": file_checks,
           "blender_version": blend_io.blender_version(), "engine_version": cartoon_engine.__version__}
    write_json(ctx.out_dir / "publish_validation.json", doc)
    ctx.progress(1.0, "validated" if not failed else "validation failed")
    return {"report": doc, "stamps": {k: v for k, v in stamped.items() if isinstance(v, (str, int, float, list))}}


def run_prop(ctx: JobContext) -> dict[str, Any]:
    from cartoon_engine.environment.check import check_prop
    from cartoon_engine.environment.library import (
        file_contents,
        link_collections,
        prop_file_alone,
        stamps,
    )

    blend, sha = _input(ctx, "prop.blend")
    rp = resolve_prop(PropSpecification.model_validate(ctx.envelope.params["spec"]))
    contents = file_contents(blend)
    blend_io.reset_empty()
    linked = link_collections(blend, ("PROP_",))["PROP_"]
    checks = check_prop(next(iter(linked.objects)), rp)
    report = {"checks": checks, "failed": [c["name"] for c in checks if not c["ok"]]}
    return _finish(ctx, "prop", rp.name, rp.digest(), sha, report, stamps(linked), contents,
                   prop_file_alone(contents), linked)


def run_environment(ctx: JobContext) -> dict[str, Any]:
    from cartoon_engine.environment.check import check_environment
    from cartoon_engine.environment.library import (
        environment_file_alone,
        file_contents,
        link_collections,
        stamps,
    )

    blend, sha = _input(ctx, "environment.blend")
    env = resolve_environment(EnvironmentSpecification.model_validate(ctx.envelope.params["spec"]))
    contents = file_contents(blend)
    blend_io.reset_empty()
    linked = link_collections(blend, ("ENV_", "LIGHTS_"))
    report = check_environment(linked["ENV_"], linked["LIGHTS_"], env)
    report["layout"] = env.layout()
    return _finish(ctx, "environment", env.name, env.digest(), sha, report, stamps(linked["ENV_"]), contents,
                   environment_file_alone(contents), linked["ENV_"])
