"""Layout measured in Blender (Phase 7): the contract placed everything on paper; this
measures what was built.

Prop: its size in its own axes is the contract's size, and it stands on z = 0.

Environment:

* ``dressing_size:<id>``: each dressing item measures the size the contract gave it;
* ``grounded:<id>``: it stands on the floor, or on the top of the item it is ``on``;
* ``inside:<id>``: every vertex of a floor item is inside the walls;
* ``no_overlap:<a>+<b>``: solid floor items do not intersect (BVH triangle overlap) and
  their measured footprints are apart;
* ``mark:<name>``: a character can stand there (measured footprints, its radius) and the
  first thing straight down from head height is the floor;
* ``door:<n>``: the floor inside the door is free and a ray through the doorway at hip
  height meets no wall;
* ``camera:<name>``: the camera's line to its look-at point is clear (the cutaway wall
  does not count) for at least 80 % of the way;
* ``lights``: the rig has a sun and, indoors with a lamp-lit preset, ceiling lights.
"""

from __future__ import annotations

import math

import numpy as np
from studio_contracts.environment import (
    CHARACTER_RADIUS_M,
    ResolvedEnvironment,
    ResolvedProp,
    contains,
)

from cartoon_engine.environment.props import measure

SIZE_TOL = 0.02  # share
SIZE_ABS = 0.006  # metres
Z_TOL = 0.006


def _check(name: str, ok: bool, **detail) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def size_ok(measured, expected) -> bool:
    return all(e * 0.85 - SIZE_ABS <= m <= e * (1 + SIZE_TOL) + SIZE_ABS for m, e in zip(measured, expected,
                                                                                          strict=True))


def check_prop(obj, rp: ResolvedProp) -> list[dict]:
    m = measure(obj)
    return [_check("size", size_ok(m["size"], rp.size), measured=m["size"], expected=list(rp.size)),
            _check("grounded", abs(m["bottom_z"]) <= Z_TOL, bottom_z=m["bottom_z"]),
            _check("geometry", m["faces"] > 0 and len(obj.data.materials) == 3, faces=m["faces"],
                   materials=len(obj.data.materials))]


def _world_verts(obj) -> np.ndarray:
    n = len(obj.data.vertices)
    co = np.empty(n * 3)
    obj.data.vertices.foreach_get("co", co)
    m = np.array(obj.matrix_world)
    return co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]


def _bvh(obj):
    """A BVH of the object in world space (``FromObject`` would be in its own space)."""
    from mathutils.bvhtree import BVHTree

    verts = [tuple(v) for v in _world_verts(obj)]
    return BVHTree.FromPolygons(verts, [tuple(p.vertices) for p in obj.data.polygons])


def _ray(scene, origin, direction, distance, skip_cutaway=True):
    """First hit along the ray: (object, distance) or (None, None); the cutaway wall is passed through."""
    import bpy
    from mathutils import Vector

    dg = bpy.context.evaluated_depsgraph_get()
    o, dvec = Vector(origin), Vector(direction).normalized()
    travelled = 0.0
    for _ in range(8):
        hit, loc, _n, _i, obj, _m = scene.ray_cast(dg, o, dvec, distance=distance - travelled)
        if not hit:
            return None, None
        step = (loc - o).length
        travelled += step
        if skip_cutaway and obj.get("studio_cutaway"):
            o = loc + dvec * 1e-3
            travelled += 1e-3
            continue
        return obj, travelled
    return None, None


def check_environment(coll, lights, env: ResolvedEnvironment) -> dict:
    import bpy

    bpy.context.view_layer.update()
    scene = bpy.context.scene
    objs = {o.name: o for o in coll.objects}
    w, d, _h = env.size
    checks: list[dict] = []
    measured: dict[str, dict] = {}
    for it in env.dressing:
        obj = objs[f"DRESS_{it.id}"]
        m = measure(obj)
        measured[it.id] = m
        checks.append(_check(f"dressing_size:{it.id}", size_ok(m["size"], it.prop.size), measured=m["size"],
                             expected=list(it.prop.size)))
        checks.append(_check(f"grounded:{it.id}", abs(m["bottom_z"] - it.z) <= Z_TOL, bottom_z=m["bottom_z"],
                             expected=it.z, on=it.on))
        if it.on is None:
            v = _world_verts(obj)
            over = float(max(np.abs(v[:, 0]).max() - w / 2, np.abs(v[:, 1]).max() - d / 2))
            checks.append(_check(f"inside:{it.id}", over <= 0.001, overshoot_m=round(over, 4)))

    def footprint(it):
        m = measured[it.id]
        lc = m["local_centre"]
        a = math.radians(it.rotation_deg)
        cx = it.at[0] + lc[0] * math.cos(a) - lc[1] * math.sin(a)
        cy = it.at[1] + lc[0] * math.sin(a) + lc[1] * math.cos(a)
        return (cx, cy, m["size"][0], m["size"][1], it.rotation_deg)

    from studio_contracts.environment import overlap

    solids = [it for it in env.dressing if it.prop.solid and it.on is None]
    floor_items = [it for it in env.dressing if it.on is None]
    for i, a in enumerate(floor_items):
        for b in floor_items[i + 1:]:
            if not (a.prop.solid or b.prop.solid):
                continue
            ta, tb = _bvh(objs[f"DRESS_{a.id}"]), _bvh(objs[f"DRESS_{b.id}"])
            hits = len(ta.overlap(tb))
            apart = not overlap(footprint(a), footprint(b))
            checks.append(_check(f"no_overlap:{a.id}+{b.id}", hits == 0 and apart, triangle_hits=hits,
                                 footprints_apart=apart))

    def free(x, y) -> bool:
        if abs(x) > w / 2 - CHARACTER_RADIUS_M or abs(y) > d / 2 - CHARACTER_RADIUS_M:
            return False
        return not any(contains(footprint(it), x, y, CHARACTER_RADIUS_M) for it in solids)

    for mk in env.marks:
        if mk.kind == "seat":
            seat = next((it for it in env.dressing if it.id == mk.item), None)
            checks.append(_check(f"mark:{mk.name}", seat is not None and abs(
                (seat.prop.surface_z("seat") or 0) - mk.at[2]) < 1e-6, kind="seat", item=mk.item))
            continue
        obj, dist = _ray(scene, (mk.at[0], mk.at[1], 1.7), (0, 0, -1), 3.0)
        floor_first = obj is not None and obj.get("studio_role") in ("floor", "ground")
        ok = free(mk.at[0], mk.at[1]) and floor_first
        checks.append(_check(f"mark:{mk.name}", ok, free_floor=free(mk.at[0], mk.at[1]),
                             first_hit_below=obj.name if obj else None))

    normal = {"north": (0, 1), "south": (0, -1), "east": (1, 0), "west": (-1, 0)}
    for i, op in enumerate(o for o in env.openings if o.kind == "door"):
        nx, ny = normal[op.wall]
        inside = (op.offset_m, ny * (d / 2 - 0.45)) if nx == 0 else (nx * (w / 2 - 0.45), op.offset_m)
        obj, dist = _ray(scene, (inside[0], inside[1], 1.0), (nx, ny, 0), 1.2, skip_cutaway=False)
        checks.append(_check(f"door:{i}", free(*inside) and obj is None, free_floor=free(*inside),
                             wall=op.wall, hit=obj.name if obj else None))

    for cm in env.cameras:
        v = np.array(cm.look_at) - np.array(cm.at)
        dist = float(np.linalg.norm(v))
        obj, hit = _ray(scene, cm.at, v, dist)
        clear = obj is None or hit >= 0.8 * dist
        checks.append(_check(f"camera:{cm.name}", clear, distance_m=round(dist, 3),
                             first_hit=obj.name if obj else None, hit_m=round(hit, 3) if hit else None))

    names = [o.name for o in lights.objects]
    has_sun = any(o.type == "LIGHT" and o.data.type == "SUN" for o in lights.objects)
    want_ceiling = env.kind == "room" and env.lighting.ceiling_energy_w > 0
    has_ceiling = any("_ceiling_" in n for n in names)
    checks.append(_check("lights", has_sun and has_ceiling == want_ceiling, lights=names, preset=env.lighting.key))
    failed = [c["name"] for c in checks if not c["ok"]]
    return {"ok": not failed, "failed": failed, "checks": checks,
            "measured": {k: {"size": v["size"], "bottom_z": v["bottom_z"]} for k, v in measured.items()}}
