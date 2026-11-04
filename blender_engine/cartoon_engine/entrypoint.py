"""Entry point for every Blender job.

Inside the Blender binary (production workers)::

    blender -b --factory-startup --disable-autoexec -noaudio --python-exit-code 1 \\
        --python cartoon_engine/entrypoint.py -- /work/<job_id>/job.json

With the ``bpy`` wheel (tests, development)::

    python cartoon_engine/entrypoint.py /work/<job_id>/job.json

Always writes ``result.json`` next to ``job.json``; exits 0 on success and 1 on a handled
failure. A crash leaves no result file, which the worker treats as retryable.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _job_path(argv: list[str]) -> Path:
    args = argv[argv.index("--") + 1 :] if "--" in argv else argv[1:]
    if len(args) != 1:
        raise SystemExit("usage: entrypoint.py [--] <job.json>")
    return Path(args[0])


def main(argv: list[str]) -> int:
    job_path = _job_path(argv)
    workspace = job_path.parent
    # Run as a script, this file's directory (the package itself) heads sys.path; replace it
    # with the package's parent so ``cartoon_engine`` imports as a package.
    here = str(Path(__file__).resolve().parent)
    if sys.path and sys.path[0] == here:
        sys.path[0] = str(Path(here).parent)

    import bpy  # noqa: F401  (inside Blender this is already loaded; with the wheel it loads here)

    from cartoon_engine import __version__ as engine_version
    from cartoon_engine.core import blend_io
    from cartoon_engine.core.context import JobContext, read_envelope, write_json
    from cartoon_engine.jobs import handlers
    from studio_contracts.jobs import JobResult

    result: JobResult
    try:
        envelope = read_envelope(job_path)
        handler = handlers().get(envelope.type.value)
        if handler is None:
            raise ValueError(f"no handler for job type {envelope.type!r}")
        ctx = JobContext(envelope=envelope, workspace=workspace)
        data = handler(ctx)
        result = JobResult(
            status="succeeded", outputs=ctx.outputs, data=data,
            blender_version=blend_io.blender_version(), engine_version=engine_version,
        )
    except Exception as exc:
        traceback.print_exc()
        result = JobResult(
            status="failed", error=f"{type(exc).__name__}: {exc}",
            blender_version=blend_io.blender_version(), engine_version=engine_version,
        )
    write_json(workspace / "result.json", result.model_dump(mode="json"))
    sys.stdout.flush()
    return 0 if result.status == "succeeded" else 1


if __name__ == "__main__":
    code = main(sys.argv)
    # Inside Blender, --python-exit-code only applies to uncaught exceptions; exit explicitly.
    sys.exit(code)
