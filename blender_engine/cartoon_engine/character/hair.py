"""Hair: a shell grown out of the skull over a style-dependent hairline, with a fringe, a
rim down to the scalp and (by style) spikes or a long back panel. Seeded jitter makes the
edge look cut rather than machined; the same seed gives the same hair."""

from __future__ import annotations

import math
import random

from studio_contracts.character import ResolvedCharacter

from cartoon_engine.character.skeleton import Skeleton, Vec, add, mul, norm, sub

#: hairline latitude (degrees above the head's equator) at the front, side and back,
#: shell thickness (fraction of head height) and how much the bottom edge flares out
STYLES = {
    "short": ((34.0, 14.0, -30.0), 0.035, 0.0),
    "spiky": ((36.0, 16.0, -26.0), 0.03, 0.0),
    "bob": ((30.0, -48.0, -55.0), 0.055, 0.25),
    "long": ((30.0, -45.0, -60.0), 0.05, 0.15),
}
LON, LAT = 64, 16


def _hairline(lon_deg: float, front: float, side: float, back: float) -> float:
    a = abs(lon_deg) / 180.0  # 0 front, 0.5 side, 1 back
    if a < 0.5:
        t = 0.5 - 0.5 * math.cos(math.pi * a * 2)
        return front + (side - front) * t
    t = 0.5 - 0.5 * math.cos(math.pi * (a - 0.5) * 2)
    return side + (back - side) * t


def build_hair(sk: Skeleton, rc: ResolvedCharacter, layout, materials: dict, name: str = "hair"):
    import bpy

    if rc.hair_style == "bald" or rc.hair_style not in STYLES:
        return None
    (front, side, back), thick_f, flare = STYLES[rc.hair_style]
    rng = random.Random(rc.seed * 7919 + 17)
    hh = sk.head_h
    c = sk.head_center
    hit = layout.skull_hit
    phase = rng.uniform(0, math.tau)

    def direction(lon: float, lat: float) -> Vec:
        lo, la = math.radians(lon), math.radians(lat)
        return (math.sin(lo) * math.cos(la), -math.cos(lo) * math.cos(la), math.sin(la))

    outer: list[Vec] = []
    inner: list[Vec] = []
    for j in range(LON):
        lon = -180.0 + 360.0 * j / LON
        edge = _hairline(lon, front, side, back)
        if abs(lon) < 60:  # fringe: a scalloped front edge
            edge -= 5.0 * (0.5 + 0.5 * math.sin(lon * 0.45 + phase)) * (1 - abs(lon) / 60)
        for i in range(LAT):
            s = i / (LAT - 1)
            lat = 88.0 - (88.0 - edge) * s
            loc, n = hit(direction(lon, lat))
            t = hh * thick_f * (1.0 + 0.15 * math.sin(lon * 0.3 + lat * 0.2 + phase)) * (0.35 + 0.65 * (1 - s ** 3))
            t *= 1 + flare * s * s
            outer.append(add(loc, mul(n, t)))
            inner.append(add(loc, mul(n, 0.002 * hh)))
    verts = outer + inner
    faces: list[tuple] = []
    N = LON * LAT
    for j in range(LON):
        jn = (j + 1) % LON
        for i in range(LAT - 1):
            faces.append((j * LAT + i, j * LAT + i + 1, jn * LAT + i + 1, jn * LAT + i))
        a, b = j * LAT + LAT - 1, jn * LAT + LAT - 1
        faces.append((a, N + a, N + b, b))  # rim down to the scalp
    top = len(verts)
    loc, n = hit((0.0, 0.0, 1.0))
    verts.append(add(loc, mul(n, hh * thick_f)))
    faces += [(top, jn * LAT, j * LAT) for j, jn in ((j, (j + 1) % LON) for j in range(LON))]

    if rc.hair_style == "spiky":
        _spikes(verts, faces, hit, hh, rng)
    if rc.hair_style == "long":
        _back_panel(verts, faces, sk, hit, hh, back, thick_f)

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate()
    _outward_normals(mesh)
    mesh.materials.append(materials["hair"])
    for p in mesh.polygons:
        p.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    sub_mod = obj.modifiers.new("subsurf", "SUBSURF")
    sub_mod.levels = 1
    sub_mod.render_levels = 2
    return obj


def _spikes(verts, faces, hit, hh, rng) -> None:
    for _ in range(rng.randint(9, 13)):
        lon, lat = rng.uniform(-150, 150), rng.uniform(35, 85)
        lo, la = math.radians(lon), math.radians(lat)
        loc, n = hit((math.sin(lo) * math.cos(la), -math.cos(lo) * math.cos(la), math.sin(la)))
        up = norm(add(n, (0.0, 0.25, 0.5)))  # spikes sweep back and up
        tip = add(loc, mul(up, hh * rng.uniform(0.2, 0.3)))
        side = norm((up[1], -up[0], 0.0)) if abs(up[2]) < 0.99 else (1.0, 0.0, 0.0)
        side2 = norm((up[1] * side[2] - up[2] * side[1], up[2] * side[0] - up[0] * side[2], up[0] * side[1] - up[1] * side[0]))
        r = hh * rng.uniform(0.07, 0.1)
        base = len(verts)
        for k in range(5):
            a = math.tau * k / 5
            verts.append(add(sub(loc, mul(n, 0.01 * hh)), add(mul(side, r * math.cos(a)), mul(side2, r * math.sin(a)))))
        verts.append(tip)
        for k in range(5):
            faces.append((base + k, base + (k + 1) % 5, base + 5))


def _back_panel(verts, faces, sk: Skeleton, hit, hh, back_lat, thick_f) -> None:
    """Hair falling from the back hairline to below the shoulders."""
    cols, rows = 13, 8
    bottom_z = sk.j("chest")[2]
    base = len(verts)
    for i in range(rows):
        s = i / (rows - 1)
        for j in range(cols):
            lon = 180.0 + (-75.0 + 150.0 * j / (cols - 1))
            lo, la = math.radians(lon), math.radians(back_lat + 8)
            loc, n = hit((math.sin(lo) * math.cos(la), -math.cos(lo) * math.cos(la), math.sin(la)))
            p = add(loc, mul(n, hh * thick_f))
            x = p[0] * (1 + 0.25 * s)
            y = p[1] + hh * 0.06 * s
            z = p[2] + (bottom_z - p[2]) * s
            verts.append((x, y, z))
    for i in range(rows - 1):
        for j in range(cols - 1):
            a = base + i * cols + j
            faces.append((a, a + 1, a + cols + 1, a + cols))


def _outward_normals(mesh) -> None:
    """Consistent winding (the pole fan, rim and spikes are built in different orders)."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()
