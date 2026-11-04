"""Phase 1 acceptance job: build a small scene, save it, reopen it and check it.

Proves that a background job can run Blender, that the file Blender writes is valid, and
(through the worker) that it reaches object storage with its checksum.
"""

from __future__ import annotations

from typing import Any

from studio_contracts.jobs import BlenderSmokeParams

from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext


def _build(name: str, size: float) -> None:
    import bmesh
    import bpy

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    scene = bpy.context.scene
    scene.collection.objects.link(obj)
    scene.render.fps = 24
    scene.unit_settings.system = "METRIC"


def run(ctx: JobContext) -> dict[str, Any]:
    import bpy

    params = BlenderSmokeParams.model_validate(ctx.envelope.params)
    ctx.progress(0.05, "resetting to factory settings")
    blend_io.reset_empty()
    ctx.progress(0.3, "building scene")
    _build(params.object_name, params.size_m)
    bpy.context.scene["studio_job_id"] = str(ctx.envelope.job_id)

    path = ctx.out_dir / "smoke.blend"
    ctx.progress(0.6, "saving")
    blend_io.save(path)

    ctx.progress(0.8, "reopening to verify")
    blend_io.open_file(path)
    obj = bpy.data.objects.get(params.object_name)
    if obj is None or obj.type != "MESH":
        raise RuntimeError(f"reopened file has no mesh object {params.object_name!r}")
    if bpy.context.scene.get("studio_job_id") != str(ctx.envelope.job_id):
        raise RuntimeError("reopened file lost its studio_job_id property")
    dims = [round(d, 4) for d in obj.dimensions]

    ctx.output(path, "blend", "application/x-blender")
    ctx.progress(1.0, "done")
    return {"object": obj.name, "vertices": len(obj.data.vertices), "dimensions_m": dims}
