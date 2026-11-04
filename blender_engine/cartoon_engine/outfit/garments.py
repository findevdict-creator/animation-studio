"""Garment geometry on a reference body (stand-in catalog ``GARM-*@0.1``).

Skinned garments are cut from the body surface itself: the region the garment covers is
selected by the same limb segments that weight the body, hems and cuffs are exact plane
cuts, and the surface is moved out along its normals by the garment's clearance (its ease),
smoothed and pushed clear of the body and of the layers below. Weights come from the body
weighting function at the point of the body each garment vertex was cut from, so cloth and
skin follow the bones together. A Solidify modifier after the Armature modifier gives the
fabric its thickness without changing under deformation.

Rigid pieces (cap, backpack) are modelled from primitives and bound with weight 1 to the
bone of a rig-profile attachment point.

These are procedural stand-ins, like the character template: good enough to prove fitting,
binding, masking and the fit check, not production garments.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from studio_contracts.outfit import ResolvedPiece
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1

from cartoon_engine.character.materials import hex_to_linear
from cartoon_engine.character.rig import SPLIT_BONES, body_weights
from cartoon_engine.character.skeleton import Skeleton, lerp, norm, sub
from cartoon_engine.outfit import fitspace

#: fitting order: each layer is pushed clear of the ones before it
LAYER = {"footwear": 0, "lower": 1, "upper": 2, "gloves": 2, "headwear": 3, "accessory": 4}
RIG_BONES = frozenset({*CARTOON_HUMANOID_V1.required_deform_bones, *(f"{b}.001" for b in SPLIT_BONES)})
WEIGHT_SMOOTHING = 6
HEM_PIN_RINGS = 4  # edge rings next to a hem that follow the skin's weights (skin shows there)
MIN_ISLAND = 0.03  # share of faces; smaller islands left by the cut are dropped


@dataclass
class Cut:
    """Remove the faces of ``regions`` on the positive side of a plane (and split the faces
    the plane crosses first, so the edge is straight)."""

    co: tuple[float, float, float]
    no: tuple[float, float, float]
    regions: frozenset[str]
    #: only faces within this distance of the vertical axis through ``axis_xy``
    radius: float | None = None
    axis_xy: tuple[float, float] = (0.0, 0.0)

    def near(self, c: np.ndarray) -> np.ndarray:
        if self.radius is None:
            return np.ones(len(c), dtype=bool)
        return np.hypot(c[:, 0] - self.axis_xy[0], c[:, 1] - self.axis_xy[1]) < self.radius


@dataclass
class Coverage:
    keep: frozenset[str]
    cuts: list[Cut] = field(default_factory=list)
    #: faces on the positive side of a trim plane take the trim colour; trim planes are cut
    #: too, so the colour edge is straight
    trims: list[Cut] = field(default_factory=list)


def edge_trims(cuts: list[Cut], width: float) -> list[Cut]:
    """A band of ``width`` inside every cut: cuffs, hems, collar and waistband."""
    out = []
    for c in cuts:
        co = tuple(np_ - n * width for np_, n in zip(c.co, c.no))
        out.append(Cut(co, c.no, c.regions, c.radius, c.axis_xy))
    return out


def _sided(*names: str) -> frozenset[str]:
    return frozenset(f"{n}.{s}" for n in names for s in "LR")


def _limb_cut(sk: Skeleton, region: str, start: str, end: str, t: float) -> list[Cut]:
    cuts = []
    for s in "LR":
        a, b = sk.j(f"{start}.{s}"), sk.j(f"{end}.{s}")
        cuts.append(Cut(lerp(a, b, t), norm(sub(b, a)), frozenset({f"{region}.{s}"})))
    return cuts


def _torso_cuts(sk: Skeleton, hem_z: float | None, waist_z: float | None, collar: bool) -> list[Cut]:
    cuts = []
    torso = frozenset({"torso"})
    if hem_z is not None:  # tops: drop what is below the hem
        cuts.append(Cut((0.0, 0.0, hem_z), (0.0, 0.0, -1.0), torso))
    if waist_z is not None:  # bottoms: drop what is above the waistband
        cuts.append(Cut((0.0, 0.0, waist_z), (0.0, 0.0, 1.0), torso))
    if collar:
        nb = sk.j("neck_base")
        neck_r = max(sk.radii["neck_base"])
        cuts.append(Cut((0.0, nb[1], nb[2] + 0.2 * neck_r), (0.0, 0.0, 1.0), torso,
                        radius=2.4 * neck_r, axis_xy=(0.0, nb[1])))
    return cuts


def coverage(piece: ResolvedPiece, sk: Skeleton) -> Coverage:
    J = sk.joints
    p = piece.params
    waist_z = J["spine1"][2]
    hip_z = J["pelvis"][2] - 0.2 * (J["pelvis"][2] - J["crotch"][2])
    band_w = 0.012 * sk.height
    if piece.key == "tshirt":
        hem = waist_z + (hip_z - waist_z) * p["hem"]
        keep = frozenset({"torso"}) | _sided("upper_arm")
        cuts = [*_torso_cuts(sk, hem, None, True), *_limb_cut(sk, "upper_arm", "shoulder", "elbow", p["sleeve_length"])]
        return Coverage(keep, cuts, edge_trims(cuts, band_w))
    if piece.key in ("longsleeve", "sweater"):
        hem = waist_z + (hip_z - waist_z) * p["hem"]
        keep = frozenset({"torso"}) | _sided("upper_arm", "forearm")
        cuts = [*_torso_cuts(sk, hem, None, True), *_limb_cut(sk, "forearm", "elbow", "wrist", p["sleeve_length"])]
        return Coverage(keep, cuts, edge_trims(cuts, band_w * (1.6 if piece.key == "sweater" else 1.0)))
    band = J["pelvis"][2] + (J["spine1"][2] - J["pelvis"][2]) * 0.55
    if piece.key == "shorts":
        cuts = [*_torso_cuts(sk, None, band, False), *_limb_cut(sk, "thigh", "hip", "knee", p["length"])]
        return Coverage(frozenset({"torso"}) | _sided("thigh"), cuts, edge_trims(cuts[:1], band_w * 1.5))
    if piece.key == "trousers":
        cuts = [*_torso_cuts(sk, None, band, False), *_limb_cut(sk, "shin", "knee", "ankle", p["length"])]
        return Coverage(frozenset({"torso"}) | _sided("thigh", "shin"), cuts, edge_trims(cuts[:1], band_w * 1.5))
    if piece.key in ("sneakers", "boots"):
        sole = Cut((0.0, 0.0, sk.j("ankle.L")[2] * 0.55), (0.0, 0.0, -1.0), _sided("foot", "shin"))
        return Coverage(_sided("foot", "shin"), _limb_cut(sk, "shin", "ankle", "knee", p["collar"]), [sole])
    raise KeyError(f"no skinned builder for {piece.key!r}")


# ------------------------------------------------------------------ mesh helpers
def mesh_arrays(mesh) -> tuple[np.ndarray, list[tuple[int, ...]]]:
    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    return co.reshape(-1, 3), [tuple(p.vertices) for p in mesh.polygons]


def edges_of(polys: list[tuple[int, ...]]) -> tuple[np.ndarray, np.ndarray]:
    """(unique edges (m, 2), boundary flag per edge)."""
    count: dict[tuple[int, int], int] = {}
    for poly in polys:
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            k = (a, b) if a < b else (b, a)
            count[k] = count.get(k, 0) + 1
    keys = list(count)
    return np.array(keys, dtype=int).reshape(-1, 2), np.array([count[k] == 1 for k in keys])


def vertex_normals(co: np.ndarray, polys: list[tuple[int, ...]]) -> np.ndarray:
    n = np.zeros_like(co)
    for poly in polys:
        pts = co[list(poly)]
        fn = np.zeros(3)
        for i in range(len(poly)):  # Newell
            a, b = pts[i], pts[(i + 1) % len(poly)]
            fn += np.array([(a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]), (a[0] - b[0]) * (a[1] + b[1])])
        n[list(poly)] += fn
    return n / np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]


def smooth(co: np.ndarray, edges: np.ndarray, boundary: np.ndarray, iterations: int = 4,
           lam: float = 0.5, mu: float = -0.53) -> np.ndarray:
    """Taubin smoothing (no shrinking). Boundary vertices average only their boundary
    neighbours, so hems stay smooth lines where they were cut."""
    nv = len(co)
    on_b = np.zeros(nv, dtype=bool)
    on_b[edges[boundary].ravel()] = True
    src, dst = [], []
    for (a, b), bd in zip(edges, boundary):
        if bd or not on_b[a]:
            src.append(b)
            dst.append(a)
        if bd or not on_b[b]:
            src.append(a)
            dst.append(b)
    src_a, dst_a = np.array(src, dtype=int), np.array(dst, dtype=int)
    cnt = np.bincount(dst_a, minlength=nv).astype(float)
    out = co.copy()
    for _ in range(iterations):
        for f in (lam, mu):
            acc = np.zeros_like(out)
            np.add.at(acc, dst_a, out[src_a])
            avg = np.where(cnt[:, None] > 0, acc / np.maximum(cnt, 1)[:, None], out)
            out = out + f * (avg - out)
    return out


class Surface:
    """A rest-pose surface to keep clear of (body, head, a lower layer), as a BVH tree."""

    def __init__(self, co: np.ndarray, polys: list[tuple[int, ...]], offset: float = 0.0, name: str = ""):
        from mathutils.bvhtree import BVHTree

        if offset:
            co = co + vertex_normals(co, polys) * offset
        self.name = name
        self.points = co
        self.tree = BVHTree.FromPolygons([tuple(p) for p in co], polys, all_triangles=False)
        edges, boundary = edges_of(polys)
        bverts = set(edges[boundary].ravel().tolist())
        self.boundary_faces = {i for i, poly in enumerate(polys) if any(v in bverts for v in poly)}

    def signed(self, p, reach: float):
        """(signed distance along the face normal, normal, face index) or None when the
        nearest point is out of reach or on the surface's open edge."""
        hit = self.tree.find_nearest(p, reach)
        if hit[0] is None or hit[2] in self.boundary_faces:
            return None
        loc, normal, index, _ = hit
        return (p - loc).dot(normal), normal, index


def push_clear(co: np.ndarray, surfaces: list[Surface], clearance: float, reach: float = 0.04,
               only: np.ndarray | None = None) -> tuple[np.ndarray, int]:
    from mathutils import Vector

    out = co.copy()
    moved = 0
    for i in (range(len(co)) if only is None else np.nonzero(only)[0]):
        p = Vector(out[i])
        changed = False
        for _ in range(2):
            for s in surfaces:
                r = s.signed(p, reach)
                if r is not None and r[0] < clearance:
                    p = p + r[1] * (clearance - r[0])
                    changed = True
        if changed:
            out[i] = p[:]
            moved += 1
    return out, moved


def fit_surface(co: np.ndarray, polys, surfaces: list[Surface], clearance: float, rounds: int = 3) -> tuple[np.ndarray, int]:
    """Push clear, smooth, repeat; the last pass is a push so nothing ends inside."""
    edges, boundary = edges_of(polys)
    moved_total = 0
    for _ in range(rounds):
        co, moved = push_clear(co, surfaces, clearance)
        moved_total += moved
        co = smooth(co, edges, boundary, iterations=2)
    co, moved = push_clear(co, surfaces, clearance)
    return co, moved_total + moved


# ------------------------------------------------------------------ skinned garments
def cut_from_body(body_co: np.ndarray, body_polys, sk: Skeleton, cov: Coverage):
    """Faces of the body the garment covers, with straight cuts. Returns (co, polys,
    indices of trim faces)."""
    import bmesh
    import bpy

    mesh = bpy.data.meshes.new("_cut")
    mesh.from_pydata(body_co.tolist(), [], [list(p) for p in body_polys])
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bpy.data.meshes.remove(mesh)
    sa = fitspace.seg_arrays(sk)

    def classify():
        bm.faces.ensure_lookup_table()
        c = np.array([f.calc_center_median()[:] for f in bm.faces])
        keys, _, _ = fitspace.classify(sk, c, sa)
        return c, keys

    c, keys = classify()
    for cut in [*cov.cuts, *cov.trims]:
        near = cut.near(c)
        faces = [f for f, k, n in zip(bm.faces, keys, near) if n and k in cut.regions]
        if not faces:
            continue
        geom = list({*faces, *(e for f in faces for e in f.edges), *(v for f in faces for v in f.verts)})
        bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6, plane_co=cut.co, plane_no=cut.no)
        c, keys = classify()
    # parallel cuts next to existing edges leave slivers; they would spike under Solidify
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=0.0015)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges[:], dist=0.0015)
    c, keys = classify()
    drop = np.array([k not in cov.keep for k in keys])
    for cut in cov.cuts:
        inside = np.array([k in cut.regions for k in keys]) & cut.near(c)
        drop |= inside & (((c - np.array(cut.co)) @ np.array(cut.no)) > 0)
    bmesh.ops.delete(bm, geom=[f for f, d in zip(bm.faces, drop) if d], context="FACES")
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    _drop_small_islands(bm)
    bm.verts.index_update()
    c, keys = classify()
    trim = np.zeros(len(c), dtype=bool)
    for cut in cov.trims:
        inside = np.array([k in cut.regions for k in keys]) & cut.near(c)
        trim |= inside & (((c - np.array(cut.co)) @ np.array(cut.no)) > 0)
    co = np.array([v.co[:] for v in bm.verts])
    polys = [tuple(v.index for v in f.verts) for f in bm.faces]
    bm.free()
    return co, polys, np.nonzero(trim)[0].tolist()


def _drop_small_islands(bm) -> None:
    import bmesh

    bm.faces.ensure_lookup_table()
    seen: set[int] = set()
    islands = []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, island = [f], []
        seen.add(f.index)
        while stack:
            g = stack.pop()
            island.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h.index not in seen:
                        seen.add(h.index)
                        stack.append(h)
        islands.append(island)
    total = len(bm.faces)
    small = [f for isl in islands if len(isl) < MIN_ISLAND * total for f in isl]
    if small:
        bmesh.ops.delete(bm, geom=small, context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")


@dataclass
class Built:
    piece: ResolvedPiece
    co: np.ndarray
    polys: list[tuple[int, ...]]
    weights: list[dict[str, float]]
    trim: list[int]
    kind: str  # "cover" (an open shell over the body) or "solid" (a closed object)
    anchor: tuple[float, float, float] | None = None
    stats: dict = field(default_factory=dict)


def build_skinned(piece: ResolvedPiece, sk: Skeleton, body_co, body_polys, under: list[Surface]) -> Built:
    cov = coverage(piece, sk)
    co0, polys, trim = cut_from_body(body_co, body_polys, sk, cov)
    if not polys:
        raise RuntimeError(f"{piece.object_name}: the coverage selected no faces")
    co = co0 + vertex_normals(co0, polys) * piece.clearance
    edges, boundary = edges_of(polys)
    co = smooth(co, edges, boundary, iterations=3)
    co, moved = fit_surface(co, polys, under, max(piece.clearance * 0.5, 0.002))
    # the same smoothing for every layer: layers smoothed differently slide against each other
    weights = garment_weights(sk, co, edges, under[0], iterations=WEIGHT_SMOOTHING,
                              hem=np.unique(edges[boundary].ravel()))
    return Built(piece, co, polys, weights, trim if piece.trim_color else [], kind="cover",
                 stats={"vertices": len(co), "faces": len(polys), "pushed_clear": moved})


def garment_weights(sk: Skeleton, co: np.ndarray, edges: np.ndarray, body: Surface,
                    iterations: int = 3, hem: np.ndarray | None = None) -> list[dict[str, float]]:
    """Body weights at the point of the body under each garment vertex, then smoothed over
    the garment: cloth spans hollows (the armpit, the crotch) where skin weights change
    fast, and unsmoothed weights there make the cloth stretch much more than the skin.
    Smoothing fades out towards the hems (`hem`: boundary vertices), where skin stays
    visible and the cloth edge has to move with it or it sinks in when a joint bends."""
    from mathutils import Vector

    foot = np.array([body.tree.find_nearest(Vector(p))[0][:] for p in co])
    raw, _ = body_weights(sk, foot, set(RIG_BONES))
    bones = sorted({b for w in raw for b in w})
    col = {b: i for i, b in enumerate(bones)}
    W = np.zeros((len(co), len(bones)))
    for i, w in enumerate(raw):
        for b, v in w.items():
            W[i, col[b]] = v
    nv = len(co)
    cnt = np.bincount(edges.ravel(), minlength=nv).astype(float)
    rate = np.full((nv, 1), 0.5)
    if hem is not None and len(hem):
        ring = _rings(nv, edges, hem, HEM_PIN_RINGS)
        rate[:, 0] = 0.5 * np.minimum(ring, HEM_PIN_RINGS) / HEM_PIN_RINGS
    for _ in range(iterations):
        acc = np.zeros_like(W)
        np.add.at(acc, edges[:, 0], W[edges[:, 1]])
        np.add.at(acc, edges[:, 1], W[edges[:, 0]])
        avg = np.where(cnt[:, None] > 0, acc / np.maximum(cnt, 1)[:, None], W)
        W = W + rate * (avg - W)
    out = []
    for row in W:
        top = np.argsort(-row)[:4]
        total = row[top].sum()
        out.append({bones[j]: float(row[j] / total) for j in top if row[j] / total > 0.01})
    return out


def _rings(nv: int, edges: np.ndarray, start: np.ndarray, limit: int) -> np.ndarray:
    """Edge hops from the nearest start vertex, capped at `limit`."""
    ring = np.full(nv, limit, dtype=int)
    ring[start] = 0
    front = np.zeros(nv, dtype=bool)
    front[start] = True
    for k in range(1, limit):
        nxt = np.zeros(nv, dtype=bool)
        nxt[edges[:, 1][front[edges[:, 0]]]] = True
        nxt[edges[:, 0][front[edges[:, 1]]]] = True
        nxt &= ring > k
        ring[nxt] = k
        front = nxt
    return ring


# ------------------------------------------------------------------ rigid pieces
def build_cap(piece: ResolvedPiece, sk: Skeleton, under: list[Surface]) -> Built:
    import bmesh
    import bpy

    c = np.array(sk.head_center)
    h, crown, brim = sk.head_h, piece.params["crown"], piece.params["brim"]
    pad = piece.clearance + 0.004
    rx, ry, rz = sk.head_w / 2 + pad, sk.head_d / 2 + pad, h / 2 + pad
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=40, v_segments=20, radius=1.0)
    for v in bm.verts:
        v.co.x, v.co.y, v.co.z = c[0] + v.co.x * rx, c[1] + v.co.y * ry, c[2] + v.co.z * rz
    zf = c[2] + 0.22 * h - 0.06 * h * crown  # rim height at the forehead
    zb = c[2] + 0.02 * h - 0.22 * h * crown  # and at the back of the head
    slope = (zb - zf) / (2 * ry)
    no = np.array([0.0, -slope, 1.0])
    no /= np.linalg.norm(no)
    bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=1e-6,
                           plane_co=(c[0], c[1], (zf + zb) / 2), plane_no=tuple(no), clear_inner=True)
    # brim: extrude the front of the rim outwards, longest at the front
    rim = [e for e in bm.edges if e.is_boundary]
    loop = _ordered_loop(rim)
    length = (0.25 + 0.6 * brim) * sk.head_d
    out_verts = []
    for v in loop:
        d = np.array([v.co.x - c[0], v.co.y - c[1]])
        ang = np.degrees(np.arctan2(d[0], -d[1]))  # 0 at the front
        k = max(0.0, np.cos(np.radians(min(abs(ang), 80.0) * 90.0 / 80.0)))
        if abs(ang) >= 80.0 or k < 0.02:
            out_verts.append(None)
            continue
        dn = d / max(np.linalg.norm(d), 1e-9)
        L = length * k
        out_verts.append(bm.verts.new((v.co.x + dn[0] * L, v.co.y + dn[1] * L, v.co.z - 0.12 * L)))
    n = len(loop)
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        oa, ob = out_verts[i], out_verts[(i + 1) % n]
        if oa is not None and ob is not None:
            bm.faces.new((a, b, ob, oa))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.verts.index_update()
    mesh = bpy.data.meshes.new("_cap")
    bm.to_mesh(mesh)
    bm.free()
    co, polys = mesh_arrays(mesh)
    bpy.data.meshes.remove(mesh)
    if _mean_outward(co, polys, c) < 0:
        polys = [tuple(reversed(p)) for p in polys]
    co, scale = clear_rigid_cover(co, polys, c, under, piece.clearance * 0.5)
    w = {piece.attach_bone: 1.0}
    return Built(piece, co, polys, [dict(w) for _ in range(len(co))], [], kind="cover", anchor=tuple(c),
                 stats={"vertices": len(co), "faces": len(polys), "scaled_up": round(scale, 4)})


def _ordered_loop(edges) -> list:
    nxt: dict = {}
    for e in edges:
        a, b = e.verts
        nxt.setdefault(a, []).append(b)
        nxt.setdefault(b, []).append(a)
    start = min(nxt, key=lambda v: v.index)
    loop, prev, cur = [start], None, start
    while True:
        cand = [v for v in nxt[cur] if v is not prev]
        if not cand or cand[0] is start:
            break
        prev, cur = cur, cand[0]
        loop.append(cur)
    return loop


def _mean_outward(co, polys, center) -> float:
    n = vertex_normals(co, polys)
    return float(((co - center) * n).sum(axis=1).mean())


def clear_rigid_cover(co: np.ndarray, polys, center: np.ndarray, under: list[Surface], clearance: float,
                      reach: float = 0.06) -> tuple[np.ndarray, float]:
    """Scale a rigid cover (cap) about ``center`` until whatever is under it is at least
    ``clearance`` inside. Returns the new points and the scale used."""
    from mathutils import Vector

    scale = 1.0
    for _ in range(30):
        pts = center + (co - center) * scale
        shell = Surface(pts, polys)
        worst = 0.0
        for s in under:
            for q in s.points[::3]:
                r = shell.signed(Vector(q), reach)
                if r is not None and r[0] > -clearance:
                    worst = max(worst, r[0] + clearance)
        if worst <= 0:
            return pts, scale
        scale *= 1.0 + max(0.01, worst / max(np.linalg.norm(co - center, axis=1).mean(), 1e-6))
    return center + (co - center) * scale, scale


def build_backpack(piece: ResolvedPiece, sk: Skeleton, body_co: np.ndarray, under: list[Surface]) -> Built:
    import bmesh
    import bpy

    J = sk.joints
    size = piece.params["size"]
    chest_rx = sk.radii["chest"][0]
    w = 2 * chest_rx * (0.6 + 0.35 * size)
    hgt = (J["neck_base"][2] - J["spine1"][2]) * (0.6 + 0.35 * size)
    d = w * 0.42
    cz = J["spine1"][2] + (J["neck_base"][2] - J["spine1"][2]) * 0.5
    band = (np.abs(body_co[:, 0]) < w / 3) & (np.abs(body_co[:, 2] - cz) < hgt / 3)
    y_back = float(body_co[band, 1].max())
    cy = y_back + piece.clearance + d / 2
    bm = bmesh.new()

    def box(cx, cy_, cz_, sx, sy, sz, mat):
        made = bmesh.ops.create_cube(bm, size=1.0)["verts"]
        for v in made:
            v.co.x, v.co.y, v.co.z = cx + v.co.x * sx, cy_ + v.co.y * sy, cz_ + v.co.z * sz
        for f in {f for v in made for f in v.link_faces}:
            f.material_index = mat  # bevel faces take the material of their neighbours
        edges = list({e for v in made for e in v.link_edges})
        bmesh.ops.bevel(bm, geom=made + edges, offset=min(sx, sy, sz) * 0.3, segments=3, profile=0.5,
                        affect="EDGES", clamp_overlap=True)

    box(0.0, cy, cz, w, d, hgt, 0)
    box(0.0, cy + d / 2 + d * 0.08, cz - hgt * 0.18, w * 0.7, d * 0.3, hgt * 0.38, 1)
    bm.normal_update()
    bm.verts.index_update()
    mesh = bpy.data.meshes.new("_bag")
    bm.to_mesh(mesh)
    bm.free()
    co, polys = mesh_arrays(mesh)
    mats = [p.material_index for p in mesh.polygons]
    bpy.data.meshes.remove(mesh)
    anchor = (0.0, y_back, cz)
    co, moved = clear_rigid_solid(co, polys, under, piece.clearance * 0.5, direction=np.array([0.0, 1.0, 0.0]))
    trim = [i for i, m in enumerate(mats) if m == 1] if piece.trim_color else []
    wts = {piece.attach_bone: 1.0}
    return Built(piece, co, polys, [dict(wts) for _ in range(len(co))], trim, kind="solid", anchor=anchor,
                 stats={"vertices": len(co), "faces": len(polys), "moved_back_m": round(moved, 4)})


def clear_rigid_solid(co: np.ndarray, polys, under: list[Surface], clearance: float, direction: np.ndarray,
                      reach: float = 0.08) -> tuple[np.ndarray, float]:
    """Move a closed rigid piece along ``direction`` until nothing under it is inside or
    closer than ``clearance``."""
    from mathutils import Vector

    total = 0.0
    for _ in range(20):
        shell = Surface(co, polys)
        worst = 0.0
        for s in under:
            for q in s.points:
                r = shell.signed(Vector(q), reach)
                if r is not None and r[0] < clearance:
                    worst = max(worst, clearance - r[0])
        if worst <= 1e-5:
            break
        co = co + direction * worst
        total += worst
    return co, total


# ------------------------------------------------------------------ objects
def material_for(piece: ResolvedPiece, trim: bool):
    import bpy

    name = f"{piece.object_name}{'_trim' if trim else ''}"
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF") or mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    rgba = hex_to_linear(piece.trim_color if trim and piece.trim_color else piece.color)
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = piece.roughness
    bsdf.inputs["Specular IOR Level"].default_value = piece.specular
    if piece.sheen:
        bsdf.inputs["Sheen Weight"].default_value = piece.sheen
    mat.diffuse_color = rgba
    mat.roughness = piece.roughness
    mat["studio_slot"] = name
    mat["studio_fabric"] = piece.fabric
    return mat


def make_object(b: Built, name: str | None = None):
    """A mesh object with materials, weights as vertex groups, an Armature modifier (no
    target until fitted) and, for open shells, a Solidify modifier after it."""
    import bpy

    piece = b.piece
    mesh = bpy.data.meshes.new(name or piece.object_name)
    mesh.from_pydata(b.co.tolist(), [], [list(p) for p in b.polys])
    mesh.validate()
    for p in mesh.polygons:
        p.use_smooth = True
    mesh.materials.append(material_for(piece, False))
    if b.trim:
        mesh.materials.append(material_for(piece, True))
        trim = set(b.trim)
        for p in mesh.polygons:
            p.material_index = 1 if p.index in trim else 0
    obj = bpy.data.objects.new(name or piece.object_name, mesh)
    groups: dict[str, dict[float, list[int]]] = {}
    for vi, w in enumerate(b.weights):
        for bone, val in w.items():
            groups.setdefault(bone, {}).setdefault(round(val, 4), []).append(vi)
    for bone, buckets in groups.items():
        g = obj.vertex_groups.new(name=bone)
        for val, idx in buckets.items():
            g.add(idx, val, "REPLACE")
    obj.modifiers.new("rig", "ARMATURE").use_deform_preserve_volume = True
    if b.kind == "cover":
        sol = obj.modifiers.new("thickness", "SOLIDIFY")
        sol.thickness = piece.thickness
        sol.offset = 1.0
        sol.use_even_offset = False  # even offset spikes at the acute corners of hem cuts
        sol.use_quality_normals = True
        sol.use_rim = True
        sol.material_offset_rim = 1 if b.trim else 0
    obj["studio_slot"] = piece.slot
    obj["studio_garment"] = piece.garment
    obj["studio_binding"] = piece.binding
    obj["studio_kind"] = b.kind
    obj["studio_layer"] = LAYER[piece.slot]
    obj["studio_covers"] = list(piece.covers)
    obj["studio_hides"] = list(piece.hides)
    obj["studio_thickness"] = piece.thickness
    obj["studio_clearance"] = piece.clearance
    obj["studio_fabric"] = piece.fabric
    if piece.attachment:
        obj["studio_attachment_point"] = piece.attachment
        obj["studio_attach_bone"] = piece.attach_bone
    if b.anchor is not None:
        obj["studio_anchor"] = list(b.anchor)
    return obj
