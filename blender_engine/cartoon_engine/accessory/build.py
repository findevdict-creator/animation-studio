"""Accessory geometry (stand-in catalog ``ACCD-*@0.1``) and the accessory file.

Each item is one mesh with a main and an accent material, modelled from primitives around
the attachment frame of its point on the reference body: vertices are relative to the
frame's origin, in metres at the reference size. ``accessory.blend`` holds one ``ACC_*``
collection with that one object, unparented, and the stamps a wearer needs (attachment
point, reference size, placement). No character, no rig.

These are procedural stand-ins, like the garments: good enough to prove versioning,
attachment, clearing, the pose check and linking in scenes, not production props.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from studio_contracts.accessory import ResolvedAccessory

from cartoon_engine.accessory.frames import Frame, eye_radius, frame, palm_normal
from cartoon_engine.character.materials import hex_to_linear
from cartoon_engine.character.skeleton import Skeleton
from cartoon_engine.character.validate import Parts


@dataclass
class Shape:
    """Vertices, faces and a material index per face (0 main, 1 accent)."""

    co: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    mats: list = field(default_factory=list)

    def add(self, co, faces, mat: int = 0) -> None:
        base = len(self.co)
        self.co.extend([tuple(map(float, p)) for p in co])
        self.faces.extend([tuple(base + i for i in f) for f in faces])
        self.mats.extend([mat] * len(faces))


def _bm_shape(bm, shape: Shape, mat: int) -> None:
    bm.verts.index_update()
    shape.add([v.co[:] for v in bm.verts], [[v.index for v in f.verts] for f in bm.faces], mat)
    bm.free()


def ellipsoid(shape: Shape, center, radii, mat: int = 0, segs: int = 16, rings: int = 10) -> None:
    import bmesh

    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=1.0)
    for v in bm.verts:
        v.co.x, v.co.y, v.co.z = (center[0] + v.co.x * radii[0], center[1] + v.co.y * radii[1],
                                  center[2] + v.co.z * radii[2])
    _bm_shape(bm, shape, mat)


def tube_ring(shape: Shape, center, u, v, rx: float, ry: float, tube: float, mat: int = 0, n: int = 32,
              m: int = 8, power: float = 2.0, w=None) -> None:
    """A closed ring of circular section around ``center`` in the plane (u, v): an
    ellipse, or a rounded rectangle for ``power`` > 2 (superellipse)."""
    center, u, v = np.asarray(center, float), np.asarray(u, float), np.asarray(v, float)
    w = np.cross(u, v) if w is None else np.asarray(w, float)
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        c, s = math.cos(t), math.sin(t)
        e = 2.0 / power
        x = rx * math.copysign(abs(c) ** e, c)
        y = ry * math.copysign(abs(s) ** e, s)
        # outward direction in the plane, from the curve's normal
        dx = -rx * e * abs(c) ** (e - 1) * s if c else -rx * s
        dy = ry * e * abs(s) ** (e - 1) * c if s else ry * c
        tang = u * dx + v * dy
        tang /= max(np.linalg.norm(tang), 1e-9)
        out = np.cross(tang, w)
        out /= max(np.linalg.norm(out), 1e-9)
        p = center + u * x + v * y
        for k in range(m):
            a = 2 * math.pi * k / m
            pts.append(p + tube * (math.cos(a) * out + math.sin(a) * w))
    faces = [(i * m + k, i * m + (k + 1) % m, ((i + 1) % n) * m + (k + 1) % m, ((i + 1) % n) * m + k)
             for i in range(n) for k in range(m)]
    shape.add(pts, faces, mat)


def disc(shape: Shape, center, u, v, rx: float, ry: float, thick: float, mat: int, n: int = 32,
         power: float = 2.0) -> None:
    center, u, v = np.asarray(center, float), np.asarray(u, float), np.asarray(v, float)
    w = np.cross(u, v)
    ring = []
    for i in range(n):
        t = 2 * math.pi * i / n
        c, s = math.cos(t), math.sin(t)
        e = 2.0 / power
        ring.append(u * rx * math.copysign(abs(c) ** e, c) + v * ry * math.copysign(abs(s) ** e, s))
    pts = [center + p + w * thick / 2 for p in ring] + [center + p - w * thick / 2 for p in ring]
    faces = [tuple(range(n)), tuple(reversed(range(n, 2 * n)))]
    faces += [(i, n + i, n + (i + 1) % n, (i + 1) % n) for i in range(n)]
    shape.add(pts, faces, mat)


def box(shape: Shape, center, size, mat: int = 0, bevel: float = 0.25) -> None:
    import bmesh

    bm = bmesh.new()
    made = bmesh.ops.create_cube(bm, size=1.0)["verts"]
    for v in made:
        v.co.x, v.co.y, v.co.z = center[0] + v.co.x * size[0], center[1] + v.co.y * size[1], center[2] + v.co.z * size[2]
    if bevel:
        bmesh.ops.bevel(bm, geom=list(bm.verts) + list(bm.edges), offset=min(size) * bevel, segments=2,
                        profile=0.5, affect="EDGES", clamp_overlap=True)
    _bm_shape(bm, shape, mat)


# ------------------------------------------------------------------ items
def _half_width_at(objs, origin: np.ndarray, band: float, depth: float) -> float:
    """Largest |x| of ``objs`` in a horizontal band around ``origin`` from the face back by ``depth``."""
    import bpy

    bpy.context.view_layer.update()
    best = 0.0
    for obj in objs:
        if obj is None:
            continue
        m = np.array(obj.matrix_world)
        co = np.empty(len(obj.data.vertices) * 3)
        obj.data.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
        sel = (np.abs(co[:, 2] - origin[2]) < band) & (co[:, 1] < origin[1] + depth)
        if sel.any():
            best = max(best, float(np.abs(co[sel, 0]).max()))
    return best


def _glasses(ra: ResolvedAccessory, f: Frame, sk: Skeleton, parts: Parts) -> Shape:
    p = ra.params
    E = f.size
    s = Shape()
    r = E * (0.30 + 0.12 * p["frame_size"])
    power = 2.0 + 4.0 * (1.0 - p["roundness"])
    tube = E * (0.025 + 0.03 * p["thickness"])
    lens_y = -(eye_radius(parts.eyes["L"]) + 2.2 * tube + 0.004)
    temple_len = sk.head_d * 0.4
    # temples run back outside the head (and hair) at the height of the frame
    half = _half_width_at([parts.head, parts.hair], f.origin, r, temple_len + abs(lens_y))
    ux, uz = np.array([1.0, 0, 0]), np.array([0, 0, 1.0])
    for side in (-1, 1):
        c = np.array([side * E / 2, lens_y, 0.0])
        tube_ring(s, c, ux, uz, r, r * 0.82, tube, mat=0, power=power, w=np.array([0, -1.0, 0]))
        disc(s, c, ux, uz, r * 0.96, r * 0.8, tube * 0.4, mat=1, power=power)
        x0 = side * (E / 2 + r)  # the outer edge of the frame
        x1 = side * max(half + tube * 2.5, abs(x0))
        # end piece from the frame out to the side of the head, then the temple back to the ear
        box(s, ((x0 + x1) / 2, lens_y, r * 0.25), (abs(x1 - x0) + tube * 2, tube * 1.6, tube * 1.6), mat=0, bevel=0.3)
        box(s, (x1, lens_y + temple_len / 2, r * 0.25), (tube * 1.6, temple_len, tube * 1.6), mat=0, bevel=0.3)
    gap = E - 2 * r
    box(s, (0.0, lens_y, r * 0.2), (gap + tube * 2, tube * 1.6, tube * 1.6), mat=0, bevel=0.3)
    return s


def _beanie(ra: ResolvedAccessory, f: Frame, sk: Skeleton, parts: Parts) -> Shape:
    import bmesh

    p = ra.params
    s = Shape()
    pad = 0.006
    rx, ry = sk.head_w / 2 + pad, sk.head_d / 2 + pad
    rz_top = sk.head_h / 2 * (1.0 + 0.35 * p["height"]) + pad
    rim_z = 0.12 * sk.head_h  # above the centre: over the ears, below the forehead top
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=40, v_segments=20, radius=1.0)
    for v in bm.verts:
        v.co.x, v.co.y = v.co.x * rx, v.co.y * ry
        v.co.z = v.co.z * (rz_top if v.co.z > 0 else sk.head_h / 2 + pad)
    bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=1e-6,
                           plane_co=(0.0, 0.0, rim_z - 0.08 * sk.head_h), plane_no=(0.0, 0.22, 1.0),
                           clear_inner=True)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    _bm_shape(bm, s, 0)
    cuff = 0.03 * sk.head_h + 0.09 * sk.head_h * p["cuff"]
    if p["cuff"] > 0.02:
        cz = rim_z - 0.08 * sk.head_h + cuff / 2
        tube_ring(s, (0.0, 0.0, cz), (1, 0, 0), (0, 1, 0), rx * 1.03, ry * 1.03, cuff / 2, mat=0, n=40, m=8)
    if p["pompom"] > 0.02:
        pr = sk.head_w * (0.08 + 0.12 * p["pompom"])
        ellipsoid(s, (0.0, 0.0, rz_top + pr * 0.7), (pr, pr, pr), mat=1)
    return s


def _watch(ra: ResolvedAccessory, f: Frame, sk: Skeleton, parts: Parts) -> Shape:
    p = ra.params
    s = Shape()
    side = ra.attach_point[-1]
    axis = f.axis
    dorsal = -palm_normal(side)
    side_v = np.cross(axis, dorsal)
    rw = f.size
    strap = rw * (0.22 + 0.25 * p["strap_width"])
    tube_ring(s, (0, 0, 0), dorsal, side_v, rw * 1.06, rw * 1.0, strap * 0.45, mat=0, n=28, m=6, w=axis)
    face_r = rw * (0.45 + 0.35 * p["face_size"])
    disc(s, dorsal * (rw * 1.06 + strap * 0.5), axis, side_v, face_r, face_r, strap * 0.6, mat=1)
    tube_ring(s, dorsal * (rw * 1.06 + strap * 0.75), axis, side_v, face_r, face_r, strap * 0.18, mat=0, n=24, m=6,
              w=dorsal)
    return s


def _tote(ra: ResolvedAccessory, f: Frame, sk: Skeleton, parts: Parts) -> Shape:
    p = ra.params
    s = Shape()
    L = f.size
    w = L * (1.3 + 0.9 * p["size"])
    h = w * 0.95
    d = w * 0.28
    handle = L * (0.35 + 0.5 * p["handle"])
    side = 1.0 if ra.attach_point.split("_")[1] == "L" else -1.0
    off = np.array([side * d * 0.9, 0.0, 0.0])  # beside the hand, away from the body
    top = off + np.array([0.0, 0.0, -handle])
    box(s, top + np.array([0.0, 0.0, -h / 2]), (d, w, h), mat=0, bevel=0.12)
    box(s, top + np.array([0.0, 0.0, -h * 0.3]), (d * 1.06, w * 1.02, h * 0.12), mat=1, bevel=0.2)
    # two straps up to the grip
    for y in (-w * 0.22, w * 0.22):
        tube_ring(s, off * 0.5 + np.array([0.0, y * 0.5, -handle / 2]), (0, 0, 1), (0, 1, 0), handle / 2 + 0.004,
                  abs(y) * 0.5 + 0.004, L * 0.025, mat=0, n=16, m=6, w=np.array([1.0, 0, 0]))
    return s


def _teddy(ra: ResolvedAccessory, f: Frame, sk: Skeleton, parts: Parts) -> Shape:
    p = ra.params
    s = Shape()
    L = f.size
    H = L * (1.6 + 1.2 * p["size"])  # standing height of the bear
    side = 1.0 if ra.attach_point.split("_")[1] == "L" else -1.0
    base = np.array([side * H * 0.22, -H * 0.05, -H * 0.15])  # the bear's back in the palm, hanging
    body = base + np.array([0.0, 0.0, -H * 0.25])
    head = base + np.array([0.0, 0.0, H * 0.12])
    hr = H * 0.2
    ellipsoid(s, body, (H * 0.2, H * 0.17, H * 0.25), 0)
    ellipsoid(s, head, (hr, hr * 0.92, hr), 0)
    ellipsoid(s, head + np.array([0.0, -hr * 0.85, -hr * 0.25]), (hr * 0.42, hr * 0.3, hr * 0.32), 1, 12, 8)
    for sx in (-1, 1):
        ellipsoid(s, head + np.array([sx * hr * 0.75, 0.0, hr * 0.75]), (hr * 0.32, hr * 0.18, hr * 0.32), 0, 12, 8)
        ellipsoid(s, body + np.array([sx * H * 0.21, -H * 0.03, H * 0.06]), (H * 0.07, H * 0.07, H * 0.15), 0, 12, 8)
        ellipsoid(s, body + np.array([sx * H * 0.1, -H * 0.06, -H * 0.24]), (H * 0.08, H * 0.09, H * 0.12), 0, 12, 8)
    return s


BUILDERS = {"glasses": _glasses, "beanie": _beanie, "watch": _watch, "tote": _tote, "teddy": _teddy}


def material(ra: ResolvedAccessory, accent: bool):
    import bpy

    name = f"{ra.object_name}{'_accent' if accent else ''}"
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF") or mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    rgba = hex_to_linear(ra.accent_color if accent and ra.accent_color else ra.color)
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = 0.1 if accent and ra.key == "glasses" else ra.roughness
    bsdf.inputs["Specular IOR Level"].default_value = ra.specular
    if ra.finish == "metal" and not accent:
        bsdf.inputs["Metallic"].default_value = 0.9
    if ra.sheen:
        bsdf.inputs["Sheen Weight"].default_value = ra.sheen
    mat.diffuse_color = rgba
    mat["studio_slot"] = name
    mat["studio_finish"] = ra.finish
    return mat


def build_accessory(ra: ResolvedAccessory, sk: Skeleton, parts: Parts, digest: str):
    """The accessory object in its own ``ACC_*`` collection (not linked to any scene),
    modelled on the reference body ``sk``/``parts``. Returns (collection, object, frame, stats)."""
    import bpy

    from cartoon_engine.outfit.fit import safe_name

    f = frame(ra.attach_point, sk, parts)
    shape = BUILDERS[ra.key](ra, f, sk, parts)
    mesh = bpy.data.meshes.new(ra.object_name)
    mesh.from_pydata(shape.co, [], [list(x) for x in shape.faces])
    mesh.validate()
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])  # outward on every closed part
    bm.to_mesh(mesh)
    bm.free()
    for poly, m in zip(mesh.polygons, shape.mats, strict=False):
        poly.use_smooth = True
        poly.material_index = m
    mesh.materials.append(material(ra, False))
    mesh.materials.append(material(ra, True))
    obj = bpy.data.objects.new(ra.object_name, mesh)
    coll = bpy.data.collections.new(f"ACC_{safe_name(ra.name)}")
    coll.objects.link(obj)
    for target in (coll, obj):
        target["studio_accessory_name"] = ra.name
        target["studio_accessory_digest"] = digest
        target["studio_item"] = ra.item
        target["studio_category"] = ra.category
        target["studio_placement"] = ra.placement
        target["studio_attach_point"] = ra.attach_point
        target["studio_attach_points"] = list(ra.attach_points)
        target["studio_rig_profile"] = ra.rig_profile
        target["studio_style_profile"] = ra.style_profile
        target["studio_reference_fit_profile"] = ra.reference_fit_profile
        target["studio_ref_size"] = f.size
    # the mesh too: a scene links only the mesh, and a wearer reads the stamps from it
    for k in ("studio_attach_point", "studio_attach_points", "studio_placement", "studio_ref_size",
              "studio_accessory_digest", "studio_item"):
        mesh[k] = obj[k]
    stats = {"vertices": len(mesh.vertices), "faces": len(mesh.polygons), "frame": f.as_dict()}
    return coll, obj, f, stats
