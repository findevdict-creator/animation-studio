"""Prop geometry (stand-in catalog ``PROPD-*@0.1``).

Each prop is one mesh with three materials (main, accent, and a fixed dark trim for wheels,
feet and stems), modelled from primitives in metres. The origin is the centre of its floor
footprint at the bottom, so placing a prop is setting its location and turning it about Z;
its front faces -Y (south), toward the default cameras, the same as characters.

These are procedural stand-ins in the way the garments and accessories are: they prove
versioning, placement, the layout checks and linking in scenes. They are not modelled
production props.
"""

from __future__ import annotations

import math

import numpy as np
from studio_contracts.environment import ResolvedProp

from cartoon_engine.accessory.build import Shape, box, ellipsoid
from cartoon_engine.character.materials import _principled

MAIN, ACCENT, TRIM = 0, 1, 2
TRIM_COLOR = "#2B2B30"


def cylinder(shape: Shape, base, radius: float, height: float, mat: int = 0, n: int = 16,
             top_radius: float | None = None) -> None:
    """Upright cylinder (or frustum) standing on ``base``."""
    bx, by, bz = base
    r1 = radius if top_radius is None else top_radius
    ring = [(math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n)) for i in range(n)]
    co = [(bx + c * radius, by + s * radius, bz) for c, s in ring] + \
         [(bx + c * r1, by + s * r1, bz + height) for c, s in ring]
    faces = [tuple(reversed(range(n))), tuple(range(n, 2 * n))]
    faces += [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    shape.add(co, faces, mat)


def wheel(shape: Shape, center, radius: float, width: float, axis: str = "x", n: int = 16) -> None:
    cx, cy, cz = center
    ring = [(math.cos(2 * math.pi * i / n) * radius, math.sin(2 * math.pi * i / n) * radius) for i in range(n)]
    if axis == "x":
        co = [(cx - width / 2, cy + a, cz + b) for a, b in ring] + [(cx + width / 2, cy + a, cz + b) for a, b in ring]
    else:
        co = [(cx + a, cy - width / 2, cz + b) for a, b in ring] + [(cx + a, cy + width / 2, cz + b) for a, b in ring]
    faces = [tuple(reversed(range(n))), tuple(range(n, 2 * n))]
    faces += [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    shape.add(co, faces, TRIM)


def _legs(s: Shape, w: float, d: float, h: float, t: float, inset: float, mat: int = MAIN) -> None:
    for x in (-1, 1):
        for y in (-1, 1):
            box(s, (x * (w / 2 - inset - t / 2), y * (d / 2 - inset - t / 2), h / 2), (t, t, h), mat, bevel=0.1)


def _table(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size
    s, top = Shape(), 0.04
    box(s, (0, 0, h - top / 2), (w, d, top), MAIN, bevel=0.2)
    _legs(s, w, d, h - top, 0.05, 0.05)
    return s


def _chair(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size
    s, seat = Shape(), rp.surface_z("seat") or h / 2
    box(s, (0, 0, seat - 0.02), (w, d, 0.04), MAIN, bevel=0.2)
    _legs(s, w, d, seat - 0.04, 0.04, 0.02)
    # the back rises at the north edge, so the chair faces south
    box(s, (0, d / 2 - 0.025, (seat + h) / 2), (w, 0.04, h - seat), MAIN, bevel=0.2)
    return s


def _desk(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size
    s = Shape()
    box(s, (0, 0, h - 0.02), (w, d, 0.04), MAIN, bevel=0.2)
    _legs(s, w, d, h - 0.04, 0.035, 0.02, ACCENT)
    box(s, (0, 0.05, h - 0.14), (w - 0.08, d - 0.14, 0.02), ACCENT, bevel=0.0)  # book tray
    return s


def _sofa(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size
    s, seat, arm = Shape(), rp.surface_z("seat") or h / 2, 0.16
    box(s, (0, 0, (seat - 0.1) / 2 + 0.04), (w, d, seat - 0.14), MAIN, bevel=0.15)
    box(s, (0, d / 2 - 0.1, (h + 0.04) / 2), (w, 0.2, h - 0.04), MAIN, bevel=0.3)
    for x in (-1, 1):
        box(s, (x * (w / 2 - arm / 2), 0, (seat + 0.18) / 2 + 0.02), (arm, d, seat + 0.18), MAIN, bevel=0.3)
    n = 2 if w < 1.6 else 3
    cw = (w - 2 * arm) / n
    for i in range(n):  # seat cushions, the accent colour
        box(s, (-w / 2 + arm + cw * (i + 0.5), -0.06, seat - 0.05), (cw - 0.02, d - 0.24, 0.1), ACCENT, bevel=0.35)
    for x in (-1, 1):
        box(s, (x * (w / 2 - 0.05), 0, 0.02), (0.05, d - 0.1, 0.04), TRIM, bevel=0.0)  # feet
    return s


def _bed(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size
    s, top = Shape(), rp.surface_z("top") or 0.8 * h
    box(s, (0, 0, 0.45 * h / 2), (w, d, 0.45 * h), MAIN, bevel=0.1)
    box(s, (0, -0.03, (0.45 * h + top) / 2), (w - 0.06, d - 0.12, top - 0.45 * h), ACCENT, bevel=0.3)
    box(s, (0, d / 2 - 0.04, h / 2), (w, 0.08, h), MAIN, bevel=0.15)  # headboard at the north end
    box(s, (0, d / 2 - 0.3, top + 0.04), (w * 0.6, 0.3, 0.08), ACCENT, bevel=0.45)  # pillow
    return s


def _bookshelf(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size
    s, t = Shape(), 0.025
    box(s, (0, d / 2 - t / 2, h / 2), (w, t, h), MAIN, bevel=0.0)
    for x in (-1, 1):
        box(s, (x * (w / 2 - t / 2), 0, h / 2), (t, d, h), MAIN, bevel=0.0)
    levels = 5
    rng = np.random.default_rng(rp.seed)
    for i in range(levels + 1):
        z = t / 2 + i * (h - t) / levels
        box(s, (0, 0, z), (w - 2 * t, d - t, t), MAIN, bevel=0.0)
        if i == levels:
            break
        x = -w / 2 + 2 * t
        gap = (h - t) / levels
        while x < w / 2 - 3 * t:  # a row of books on every shelf
            bw = float(rng.uniform(0.025, 0.05))
            bh = float(rng.uniform(0.6, 0.85)) * gap
            if x + bw > w / 2 - 2 * t:
                break
            box(s, (x + bw / 2, -0.01, z + t / 2 + bh / 2), (bw * 0.92, d * 0.7, bh), ACCENT, bevel=0.05)
            x += bw
    return s


def _books(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size
    s, n = Shape(), 3
    for i in range(n):
        k = 1 - 0.08 * i
        z = h / n * (i + 0.5)
        box(s, (0.01 * (i - 1), 0, z), (w * k, d * k, h / n * 0.96), MAIN if i != 1 else ACCENT, bevel=0.08)
    return s


def _lamp(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size
    s = Shape()
    cylinder(s, (0, 0, 0), w * 0.35, 0.03, MAIN, n=20)
    cylinder(s, (0, 0, 0.03), 0.015, h * 0.78, MAIN, n=8)
    cylinder(s, (0, 0, h * 0.75), w / 2, h * 0.25, ACCENT, n=24, top_radius=w * 0.3)
    return s


def _ball(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size
    s = Shape()
    ellipsoid(s, (0, 0, h / 2), (w / 2, d / 2, h / 2), MAIN, segs=24, rings=14)
    cylinder(s, (0, 0, h / 2 - h * 0.06), w / 2 * 1.005, h * 0.12, ACCENT, n=24)  # stripe
    return s


def _toy_car(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size  # it points along X
    s, r = Shape(), h * 0.22
    box(s, (0, 0, r + h * 0.22), (w, d * 0.86, h * 0.36), MAIN, bevel=0.3)
    box(s, (-w * 0.05, 0, r + h * 0.55), (w * 0.5, d * 0.7, h * 0.32), ACCENT, bevel=0.3)
    for x in (-1, 1):
        for y in (-1, 1):
            wheel(s, (x * w * 0.3, y * (d / 2 - d * 0.08), r), r, d * 0.16, axis="y")
    return s


def _apple(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size
    s = Shape()
    ellipsoid(s, (0, 0, h * 0.45), (w / 2, d / 2, h * 0.45), MAIN, segs=16, rings=10)
    cylinder(s, (0, 0, h * 0.85), 0.004, h * 0.15, TRIM, n=6)
    ellipsoid(s, (w * 0.12, 0, h * 0.94), (w * 0.18, w * 0.07, 0.006), ACCENT, segs=8, rings=4)
    return s


def _tree(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size
    s = Shape()
    trunk = h * 0.45
    cylinder(s, (0, 0, 0), w * 0.08, trunk + h * 0.15, MAIN, n=12, top_radius=w * 0.05)
    rng = np.random.default_rng(rp.seed)
    ellipsoid(s, (0, 0, trunk + (h - trunk) * 0.5), (w * 0.42, d * 0.42, (h - trunk) * 0.5), ACCENT, segs=18, rings=10)
    for _ in range(3):
        a = float(rng.uniform(0, 2 * math.pi))
        r = w * 0.2
        ellipsoid(s, (math.cos(a) * r, math.sin(a) * r, trunk + (h - trunk) * float(rng.uniform(0.3, 0.6))),
                  (w * 0.28, d * 0.28, (h - trunk) * 0.32), ACCENT, segs=14, rings=8)
    return s


def _bench(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size
    s, seat = Shape(), rp.surface_z("seat") or 0.55 * h
    for i in range(3):
        box(s, (0, -d / 2 + 0.06 + i * (d * 0.75 - 0.06) / 2, seat - 0.02), (w, d * 0.22, 0.035), MAIN, bevel=0.15)
    for i in range(2):
        box(s, (0, d / 2 - 0.05, seat + 0.1 + i * (h - seat - 0.15) / 1.0 * 0.5 + 0.05), (w, 0.03, 0.08), MAIN,
            bevel=0.15)
    for x in (-1, 1):
        box(s, (x * (w / 2 - 0.12), 0, (seat - 0.04) / 2), (0.05, d * 0.9, seat - 0.04), ACCENT, bevel=0.1)
        box(s, (x * (w / 2 - 0.12), d / 2 - 0.05, (seat + h) / 2), (0.05, 0.05, h - seat), ACCENT, bevel=0.1)
    return s


def _car(rp: ResolvedProp) -> Shape:
    w, d, h = rp.size  # it points along Y
    s, r = Shape(), h * 0.22
    box(s, (0, 0, r + h * 0.2), (w, d, h * 0.38), MAIN, bevel=0.2)
    box(s, (0, -d * 0.04, r + h * 0.52), (w * 0.86, d * 0.52, h * 0.34), ACCENT, bevel=0.25)
    box(s, (0, -d * 0.04, r + h * 0.7), (w * 0.88, d * 0.46, h * 0.06), MAIN, bevel=0.2)  # roof
    for x in (-1, 1):
        for y in (-1, 1):
            wheel(s, (x * (w / 2 - w * 0.06), y * d * 0.32, r), r, w * 0.14, axis="x")
    return s


BUILDERS = {"table": _table, "chair": _chair, "desk": _desk, "sofa": _sofa, "bed": _bed, "bookshelf": _bookshelf,
            "books": _books, "lamp": _lamp, "ball": _ball, "toy_car": _toy_car, "apple": _apple, "tree": _tree,
            "bench": _bench, "car": _car}


def materials(rp: ResolvedProp, prefix: str):
    main = _principled(f"{prefix}_main", rp.color, rp.roughness, rp.specular)
    accent = _principled(f"{prefix}_accent", rp.accent_color or rp.color, min(1.0, rp.roughness + 0.1), rp.specular)
    trim = _principled(f"{prefix}_trim", TRIM_COLOR, 0.6, 0.3)
    return [main, accent, trim]


def build_prop_object(rp: ResolvedProp, name: str, material_prefix: str | None = None):
    """One mesh object for ``rp``, origin at the bottom centre of its footprint."""
    import bmesh
    import bpy

    shape = BUILDERS[rp.key](rp)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(shape.co, [], [list(f) for f in shape.faces])
    mesh.validate()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()
    for poly, m in zip(mesh.polygons, shape.mats, strict=False):
        poly.material_index = m
    for mat in materials(rp, material_prefix or name):
        mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    obj["studio_item"] = rp.item
    obj["studio_prop_key"] = rp.key
    obj["studio_size"] = list(rp.size)
    obj["studio_solid"] = rp.solid
    return obj


def measure(obj) -> dict:
    """The object's size in its own axes (so a turned prop measures its real width and depth)
    and its world bottom height."""
    import bpy

    bpy.context.view_layer.update()
    n = len(obj.data.vertices)
    co = np.empty(n * 3)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3) * np.array(obj.scale)
    m = np.array(obj.matrix_world)
    world = (co / np.array(obj.scale)) @ m[:3, :3].T + m[:3, 3]
    lo, hi = co.min(axis=0), co.max(axis=0)
    return {"size": [round(float(v), 4) for v in hi - lo], "local_centre": [round(float(v), 4) for v in (lo + hi) / 2],
            "bottom_z": round(float(world[:, 2].min()), 4), "top_z": round(float(world[:, 2].max()), 4),
            "verts": n, "faces": len(obj.data.polygons)}
