"""Putting a published accessory on a character (architecture 5.3, 7.2 step 6).

The accessory's mesh (with its materials) is **linked** from ``accessory.blend``; the scene
gets a new local object that uses that mesh, so the geometry stays in the library file and
only the placement is scene state. The object is placed by mapping the reference
attachment frame onto the wearer (``T(origin) @ S(size / ref_size)``, mirrored when the
point is on the other side from the one the item was modelled on), cleared from what is
under it, then parented to the attachment bone of the wearer's rig so it follows the pose.

Clearing: a ``cover`` (hat, watch) grows about its origin until the body, hair and clothes
under it are inside with the clearance; a ``solid`` (glasses, bag, toy) moves along the
frame's push direction until nothing is inside it or closer than the clearance.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cartoon_engine.accessory.frames import frame
from cartoon_engine.character.skeleton import Skeleton
from cartoon_engine.character.validate import Parts
from cartoon_engine.outfit.garments import (
    Surface,
    clear_rigid_cover,
    clear_rigid_solid,
    mesh_arrays,
)

MAX_COVER_SCALE = 1.6


@dataclass
class Attached:
    obj: object
    point: str
    bone: str
    info: dict


def link_accessory_mesh(path: Path | str):
    """The mesh of the one ``ACC_*`` object in ``path``, linked (read-only library data)."""
    import bpy

    path = str(Path(path).resolve())
    with bpy.data.libraries.load(path, link=True, relative=True) as (src, dst):
        names = [m for m in src.meshes if m.startswith("ACC_")]
        if len(names) != 1:
            raise ValueError(f"{path} has {len(names)} ACC_ meshes, expected 1")
        dst.meshes = list(names)
    mesh = next(m for m in bpy.data.meshes if m.name == names[0] and m.library is not None
                and Path(bpy.path.abspath(m.library.filepath)).resolve() == Path(path))
    return mesh


def under_surfaces(parts: Parts, garments: list = ()) -> list[Surface]:
    """What an accessory must stay clear of, in rest-pose world space: skin, hair, eyes and
    the garments the instance wears (with their thickness)."""
    out = []
    for obj in [parts.body, parts.head, parts.hair, *parts.eyes.values(), *garments]:
        if obj is None:
            continue
        co, polys = mesh_arrays(obj.data)
        m = np.array(obj.matrix_world)
        thick = float(obj.get("studio_thickness", 0.0)) if obj.get("studio_kind") == "cover" else 0.0
        out.append(Surface(co @ m[:3, :3].T + m[:3, 3], polys, offset=thick, name=obj.name))
    return out


def _side(point: str) -> str | None:
    if point.startswith("wrist_"):
        return point[-1]
    if point.startswith("hand_"):
        return point.split("_")[1]
    return None


def clear_ring(co: np.ndarray, polys, center: np.ndarray, under: list[Surface], clearance: float,
               reach: float = 0.03) -> float:
    """Scale factor (>= 1) about ``center`` until nothing under the ring is inside its band
    or closer than ``clearance``."""
    from mathutils import Vector

    k = 1.0
    while k < MAX_COVER_SCALE:
        shell = Surface(center + (co - center) * k, polys)
        worst = 0.0
        for s in under:
            near = s.points[np.linalg.norm(s.points - center, axis=1) < float(np.linalg.norm(co - center, axis=1).max()) * k + reach]
            for q in near:
                r = shell.signed(Vector(q), reach)
                if r is not None and r[0] < clearance:
                    worst = max(worst, clearance - r[0])
        if worst <= 1e-5:
            break
        k *= 1.02
    return min(k, MAX_COVER_SCALE)


def place(mesh, point: str, parts: Parts, sk: Skeleton, under: list[Surface], clearance: float) -> tuple[np.ndarray, dict]:
    """World matrix (4x4) for the item on this wearer at rest, and what clearing did."""
    from mathutils import Vector  # noqa: F401  (Surface needs mathutils loaded)

    f = frame(point, sk, parts)
    built_on = mesh["studio_attach_point"]
    scale = f.size / float(mesh["studio_ref_size"])
    mirror = _side(point) is not None and _side(point) != _side(built_on)
    co, polys = mesh_arrays(mesh)
    sx = -scale if mirror else scale
    local = co * np.array([sx, scale, scale])
    if mirror:
        polys = [tuple(reversed(p)) for p in polys]
    world = local + f.origin
    info: dict = {"frame": f.as_dict(), "scale": round(scale, 4), "mirrored": mirror}
    if mesh["studio_placement"] == "cover":
        _, k = clear_rigid_cover(world, polys, f.origin, under, clearance)
        k = min(k, MAX_COVER_SCALE)
        info["scaled_up"] = round(k, 4)
        scale *= k
        sx = -scale if mirror else scale
        offset = np.zeros(3)
    elif mesh["studio_placement"] == "ring":
        k = clear_ring(world, polys, f.origin, under, clearance)
        info["scaled_up"] = round(k, 4)
        scale *= k
        sx = -scale if mirror else scale
        offset = np.zeros(3)
    else:
        moved_co, moved = clear_rigid_solid(world, polys, under, clearance, direction=f.push)
        info["moved_m"] = round(moved, 4)
        offset = f.push * moved
    m = np.eye(4)
    m[0, 0], m[1, 1], m[2, 2] = sx, scale, scale
    m[:3, 3] = f.origin + offset
    info["final_scale"] = round(scale, 4)
    return m, info


def bind_to_bone(obj, rig, bone_name: str, world) -> None:
    """Parent ``obj`` to ``bone_name`` keeping ``world`` as its rest-pose placement. The
    parent inverse comes from the bone's rest matrix, so the rig's current pose does not
    matter; the rig object itself must still be at its rest placement."""
    from mathutils import Matrix

    bone = rig.data.bones[bone_name]
    obj.parent = rig
    obj.parent_type = "BONE"
    obj.parent_bone = bone_name
    rest_tail = rig.matrix_world @ bone.matrix_local @ Matrix.Translation((0.0, bone.length, 0.0))
    obj.matrix_parent_inverse = rest_tail.inverted()
    obj.matrix_basis = Matrix(world)


def attach_accessory(path: Path | str, parts: Parts, sk: Skeleton, *, point: str | None = None,
                     bone: str | None = None, collection=None, name: str | None = None, garments: list = (),
                     clearance: float = 0.003) -> Attached:
    import bpy
    from mathutils import Matrix
    from studio_contracts.rig_profile import BUILTIN_RIG_PROFILES

    mesh = link_accessory_mesh(path)
    point = point or mesh["studio_attach_point"]
    allowed = list(mesh["studio_attach_points"])
    if point not in allowed:
        raise ValueError(f"{mesh.name} goes on {allowed}, not {point!r}")
    if bone is None:
        profile = BUILTIN_RIG_PROFILES[parts.rig["studio_rig_profile"]]
        bone = profile.attachment_points[point]
    obj = bpy.data.objects.new(name or mesh.name, mesh)
    (collection or bpy.context.scene.collection).objects.link(obj)
    # rest pose, rig at its own rest placement, for the frame and the clearing
    saved = parts.rig.matrix_world.copy()
    parts.rig.matrix_world = Matrix.Identity(4)
    bpy.context.view_layer.update()
    try:
        world, info = place(mesh, point, parts, sk, under_surfaces(parts, garments), clearance)
        bind_to_bone(obj, parts.rig, bone, world)
    finally:
        parts.rig.matrix_world = saved
        bpy.context.view_layer.update()
    obj["studio_accessory_point"] = point
    info.update(point=point, bone=bone, mesh=mesh.name, library=mesh.library.filepath if mesh.library else None)
    return Attached(obj, point, bone, info)


def detach_accessory(att: Attached) -> None:
    import bpy

    bpy.data.objects.remove(att.obj)
