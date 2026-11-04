"""Fit check (SRS 7 "clothing must deform correctly", architecture 9.3): pose the wearer
through a fixed suite and measure every piece.

* **Clipping**: body points a piece covers in the rest pose (inside it, away from its hems)
  must stay under it, and the cloth's outer surface may not sink under visible skin (which
  also catches a piece that is already wrong in the rest pose); the share is of all points tested plus
  the skin hidden under the piece. A point clips when it ends up outside the piece's outer surface (base
  surface plus fabric thickness). Points of lower layers are tested against upper layers the
  same way. Hidden (masked) body regions are not tested: they are not rendered.
* **Intersection** for solid accessories: body points inside the piece.
* **Stretch**: edge length posed / rest, per piece, at the 1st and 99th percentile, compared
  with the stretch of the skin the piece covers: a garment may stretch as much as that skin
  (with a margin, ``SKIN_MARGIN``) and otherwise not past the absolute limits.
* **Lift**: how much further from the body a skinned piece moves in a pose (99th percentile);
  a garment that lags behind its body shows here.

Poses are made on the rig's FK controls and its torso, chest and head controls by rotating
in world space, so the suite does not depend on bone rolls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from studio_contracts.outfit import FitThresholds
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1, RigProfile

from cartoon_engine.character.validate import Parts, Report, _reset_pose, _update
from cartoon_engine.character.validate import _evaluated as _evaluated_local
from cartoon_engine.outfit.fit import MASK_PREFIX, hidden_mask
from cartoon_engine.outfit.garments import Surface, edges_of, mesh_arrays

UP, DOWN, FWD, BACK = (0.0, 0.0, 1.0), (0.0, 0.0, -1.0), (0.0, -1.0, 0.0), (0.0, 1.0, 0.0)
FK_SWITCHES = ("upper_arm_parent.L", "upper_arm_parent.R", "thigh_parent.L", "thigh_parent.R")
HEM_BAND_RATIO = 0.012  # of the wearer's height: points this close to a hem are not "covered"
CLIP_TOLERANCE = 0.0005
SKIN_MARGIN = 1.3
STRETCH_PERCENTILE = 99.0


def _swing(rig, name: str, toward, degrees: float) -> None:
    """Rotate a pose bone about its head so its direction turns ``degrees`` toward ``toward``."""
    from mathutils import Matrix, Vector

    _update_only()
    pb = rig.pose.bones[name]
    head = pb.head.copy()
    v = (pb.tail - pb.head).normalized()
    axis = v.cross(Vector(toward))
    if axis.length < 1e-6:
        return
    rot = Matrix.Rotation(math.radians(degrees), 4, axis.normalized())
    pb.matrix = Matrix.Translation(head) @ rot @ Matrix.Translation(-head) @ pb.matrix
    _update_only()


def _turn(rig, name: str, axis, degrees: float) -> None:
    from mathutils import Matrix, Vector

    _update_only()
    pb = rig.pose.bones[name]
    head = pb.head.copy()
    rot = Matrix.Rotation(math.radians(degrees), 4, Vector(axis))
    pb.matrix = Matrix.Translation(head) @ rot @ Matrix.Translation(-head) @ pb.matrix
    _update_only()


def _update_only() -> None:
    import bpy

    bpy.context.view_layer.update()


def _arms(rig, toward, degrees: float) -> None:
    for s in "LR":
        _swing(rig, f"upper_arm_fk.{s}", toward, degrees)


POSES = {
    "rest": lambda rig: None,
    "t_pose": lambda rig: _arms(rig, UP, 50.0),
    "arms_up": lambda rig: _arms(rig, UP, 125.0),
    "arms_forward": lambda rig: (_arms(rig, FWD, 85.0), _swing(rig, "forearm_fk.L", UP, 70.0),
                                 _swing(rig, "forearm_fk.R", UP, 70.0)),
    "walk": lambda rig: (_swing(rig, "thigh_fk.L", FWD, 30.0), _swing(rig, "thigh_fk.R", BACK, 20.0),
                         _swing(rig, "shin_fk.R", BACK, 35.0), _swing(rig, "upper_arm_fk.L", BACK, 20.0),
                         _swing(rig, "upper_arm_fk.R", FWD, 20.0)),
    "sit": lambda rig: [(_swing(rig, f"thigh_fk.{s}", FWD, 80.0), _swing(rig, f"shin_fk.{s}", DOWN, 80.0))
                        for s in "LR"],
    "crouch": lambda rig: [(_swing(rig, f"thigh_fk.{s}", FWD, 95.0), _swing(rig, f"shin_fk.{s}", BACK, 110.0))
                           for s in "LR"],
    "twist": lambda rig: _turn(rig, "chest", UP, 30.0),
    "head_turn": lambda rig: (_turn(rig, "head", UP, 40.0), _turn(rig, "head", (1.0, 0.0, 0.0), -12.0)),
}


def pose(parts: Parts, name: str, profile: RigProfile = CARTOON_HUMANOID_V1) -> None:
    _reset_pose(parts, profile)
    _update(parts)
    POSES[name](parts.rig)
    _update(parts)


def set_fk(parts: Parts, fk: bool | dict) -> dict:
    """Switch arms and legs to FK (the suite poses FK controls); returns the old values."""
    old = {}
    for name in FK_SWITCHES:
        pb = parts.rig.pose.bones.get(name)
        if pb is not None and "IK_FK" in pb:
            old[name] = pb["IK_FK"]
            pb["IK_FK"] = fk.get(name, pb["IK_FK"]) if isinstance(fk, dict) else (1.0 if fk else 0.0)
    _update(parts)
    return old


@dataclass
class _Target:
    obj: object
    polys: list
    rest: np.ndarray
    edges: np.ndarray
    rest_len: np.ndarray
    kind: str
    thickness: float
    layer: int
    hem_far: np.ndarray  # per face: True when the face is away from the hems


def _evaluated(obj) -> np.ndarray:
    """Evaluated vertex positions in world space (bone-parented pieces move by their object
    matrix, skinned ones by their Armature modifier)."""
    co = _evaluated_local(obj)
    m = np.array(obj.matrix_world)
    return co @ m[:3, :3].T + m[:3, 3]


def _solidify(obj, on: bool) -> None:
    for m in obj.modifiers:
        if m.type == "SOLIDIFY":
            m.show_viewport = on


def _masks(parts: Parts, on: bool) -> None:
    for o in (parts.body, parts.head):
        for m in o.modifiers:
            if m.name.startswith(MASK_PREFIX):
                m.show_viewport = on


def fit_check(parts: Parts, garments: list, thresholds: FitThresholds, poses: tuple[str, ...],
              profile: RigProfile = CARTOON_HUMANOID_V1) -> tuple[Report, dict]:
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    r = Report()
    deform = {b.name for b in parts.rig.data.bones if b.use_deform}
    _structure(parts, garments, deform, r)

    zs = mesh_arrays(parts.body.data)[0][:, 2]
    band = HEM_BAND_RATIO * float(zs.max() - zs.min())
    for g in garments:
        _solidify(g, False)
    _masks(parts, False)
    hidden_v = {parts.body.name: hidden_mask(parts), parts.head.name: np.zeros(len(parts.head.data.vertices), dtype=bool)}
    switches = set_fk(parts, True)
    try:
        _reset_pose(parts, profile)
        _update(parts)
        targets: list[_Target] = []
        for g in garments:
            co, polys = mesh_arrays(g.data)
            rest = _evaluated(g)
            edges, boundary = edges_of(polys)
            bverts = np.unique(edges[boundary].ravel())
            far = np.ones(len(polys), dtype=bool)
            if len(bverts):
                kd = KDTree(len(bverts))
                for i, v in enumerate(bverts):
                    kd.insert(rest[v], i)
                kd.balance()
                vfar = np.array([kd.find(p)[2] >= band for p in rest])
                far = np.array([vfar[list(p)].all() for p in polys])
            lens = np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1)
            edges, lens = _measurable(edges, lens)
            targets.append(_Target(g, polys, rest, edges, lens, g["studio_kind"],
                                   float(g.get("studio_thickness", 0.0)), int(g["studio_layer"]), far))

        # points under each piece in the rest pose
        sources = {o.name: (o, _evaluated(o), ~hidden_v[o.name]) for o in (parts.body, parts.head)}
        body_tree_rest = Surface(sources[parts.body.name][1], mesh_arrays(parts.body.data)[1])
        covered: dict[str, list[tuple[str, np.ndarray]]] = {}
        for t in targets:
            shell = Surface(t.rest, t.polys)
            lists = []
            cand = [(n, co, vis) for n, (_, co, vis) in sources.items()]
            cand += [(u.obj.name, u.rest, np.ones(len(u.rest), dtype=bool)) for u in targets
                     if u.layer < t.layer and u.kind == "cover"]
            for name, co, vis in cand:
                idx = []
                for i in np.nonzero(vis)[0]:
                    hit = shell.tree.find_nearest(Vector(co[i]), 0.08 if t.kind == "solid" else 0.05)
                    if hit[0] is None:
                        continue
                    if t.kind == "solid":
                        idx.append(i)  # every nearby point is tested for intersection
                    elif hit[2] not in shell.boundary_faces and t.hem_far[hit[2]] \
                            and (Vector(co[i]) - hit[0]).dot(hit[1]) < 0:
                        idx.append(i)
                lists.append((name, np.array(idx, dtype=int)))
            covered[t.obj.name] = lists
        rest_gap = {t.obj.name: _gaps(body_tree_rest, t.rest) for t in targets if t.kind == "cover"
                    and t.obj["studio_binding"] == "skinned"}
        # the skin under each skinned garment (hidden or not): its own stretch is the yardstick
        body_rest = sources[parts.body.name][1]
        body_edges, _ = edges_of(mesh_arrays(parts.body.data)[1])
        skin_edges = {}
        hidden_under: dict[str, int] = {}
        for t in targets:
            if t.kind != "cover" or t.obj["studio_binding"] != "skinned":
                continue
            shell = Surface(t.rest, t.polys)
            under = np.zeros(len(body_rest), dtype=bool)
            for i, p in enumerate(body_rest):
                hit = shell.signed(Vector(p), 0.05)
                under[i] = hit is not None and hit[0] < 0
            e = body_edges[under[body_edges[:, 0]] & under[body_edges[:, 1]]]
            hidden_under[t.obj.name] = int((under & hidden_v[parts.body.name]).sum())
            skin_edges[t.obj.name] = _measurable(e, np.linalg.norm(body_rest[e[:, 0]] - body_rest[e[:, 1]], axis=1))

        body_polys = mesh_arrays(parts.body.data)[1]
        hidden_body = hidden_v[parts.body.name]
        # deleting a point deletes every face on it, so a face renders only if all its points stay
        visible_face = np.array([not hidden_body[list(p)].any() for p in body_polys])
        results: dict[str, dict] = {t.obj.name: {} for t in targets}
        for name in poses:
            pose(parts, name, profile)
            posed_src = {n: _evaluated(o) for n, (o, _, _) in sources.items()}
            posed_g = {t.obj.name: _evaluated(t.obj) for t in targets}
            body_tree = Surface(posed_src[parts.body.name], body_polys)
            for t in targets:
                pts = posed_g[t.obj.name]
                shell = Surface(pts, t.polys)
                n_cov = clipped = 0
                depth = 0.0
                by_source: dict[str, int] = {}
                for src_name, idx in covered[t.obj.name]:
                    co = posed_src.get(src_name)
                    if co is None:
                        co = posed_g[src_name]
                    for i in idx:
                        hit = shell.tree.find_nearest(Vector(co[i]), 0.08)
                        if hit[0] is None:
                            continue
                        n_cov += 1
                        s = (Vector(co[i]) - hit[0]).dot(hit[1])
                        d = (-s) if t.kind == "solid" else (s - t.thickness)
                        if d > CLIP_TOLERANCE:
                            clipped += 1
                            depth = max(depth, d)
                            by_source[src_name] = by_source.get(src_name, 0) + 1
                if t.kind == "cover" and t.obj["studio_binding"] == "skinned":
                    # the other direction: cloth sunk into visible skin (also catches a misfit
                    # that is already wrong in the rest pose, where nothing counts as covered)
                    for p in pts:
                        hit = body_tree.signed(Vector(p), 0.03)
                        if hit is None or not visible_face[hit[2]]:
                            continue
                        n_cov += 1
                        sunk = -hit[0] - t.thickness  # skin shows once the outer surface is under it
                        if sunk > CLIP_TOLERANCE:
                            clipped += 1
                            depth = max(depth, sunk)
                            by_source["into_body"] = by_source.get("into_body", 0) + 1
                lens = np.linalg.norm(pts[t.edges[:, 0]] - pts[t.edges[:, 1]], axis=1) / t.rest_len
                base = n_cov + hidden_under.get(t.obj.name, 0)  # skin points + cloth points tested
                entry = {"tested": n_cov, "hidden_under": hidden_under.get(t.obj.name, 0), "clipped": clipped,
                         "clip_share": round(clipped / base, 5) if base else 0.0,
                         "clip_depth_m": round(depth, 4), "clipped_by": by_source,
                         "stretch_lo": round(float(np.percentile(lens, 100 - STRETCH_PERCENTILE)), 3),
                         "stretch_hi": round(float(np.percentile(lens, STRETCH_PERCENTILE)), 3)}
                if t.obj.name in skin_edges and len(skin_edges[t.obj.name][0]):
                    e, rl = skin_edges[t.obj.name]
                    bp = posed_src[parts.body.name]
                    sl = np.linalg.norm(bp[e[:, 0]] - bp[e[:, 1]], axis=1) / rl
                    entry["skin_stretch_lo"] = round(float(np.percentile(sl, 100 - STRETCH_PERCENTILE)), 3)
                    entry["skin_stretch_hi"] = round(float(np.percentile(sl, STRETCH_PERCENTILE)), 3)
                if t.obj.name in rest_gap:
                    entry["lift_m"] = round(float(np.percentile(_gaps(body_tree, pts) - rest_gap[t.obj.name], 99)), 4)
                results[t.obj.name][name] = entry
    finally:
        _reset_pose(parts, profile)
        set_fk(parts, switches)
        for g in garments:
            _solidify(g, True)
        _masks(parts, True)

    for gname, per_pose in results.items():
        if not per_pose:
            continue
        clip_bad = {p: e for p, e in per_pose.items() if e["clip_share"] > thresholds.max_clip_share
                    or e["clip_depth_m"] > thresholds.max_clip_depth_m}
        r.add(f"no_clipping:{gname}", not clip_bad, poses=sorted(clip_bad),
              worst_share=max(e["clip_share"] for e in per_pose.values()),
              worst_depth_m=max(e["clip_depth_m"] for e in per_pose.values()))
        # a garment may stretch as much as the skin it covers (plus a margin), never past the
        # absolute limits otherwise
        stretch_bad = sorted(
            p for p, e in per_pose.items()
            if e["stretch_hi"] > max(thresholds.max_edge_stretch, e.get("skin_stretch_hi", 0) * SKIN_MARGIN)
            or e["stretch_lo"] < min(thresholds.min_edge_stretch, e.get("skin_stretch_lo", 1) / SKIN_MARGIN))
        r.add(f"deformation:{gname}", not stretch_bad, poses=stretch_bad,
              stretch=[min(e["stretch_lo"] for e in per_pose.values()),
                       max(e["stretch_hi"] for e in per_pose.values())],
              skin_stretch=[min(e.get("skin_stretch_lo", 1.0) for e in per_pose.values()),
                            max(e.get("skin_stretch_hi", 1.0) for e in per_pose.values())])
        lifts = [e["lift_m"] for e in per_pose.values() if "lift_m" in e]
        if lifts:
            r.add(f"stays_on_body:{gname}", max(lifts) <= thresholds.max_lift_m, worst_lift_m=max(lifts))
    return r, results


def _measurable(edges: np.ndarray, lens: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Edges long enough for a length ratio to mean something: plane cuts leave slivers
    whose ratios explode while the cloth around them barely moves."""
    if not len(lens):
        return edges, lens
    keep = lens >= 0.25 * float(np.median(lens))
    return edges[keep], lens[keep]


def _gaps(surface: Surface, pts: np.ndarray) -> np.ndarray:
    from mathutils import Vector

    return np.array([surface.tree.find_nearest(Vector(p))[3] for p in pts])


def _structure(parts: Parts, garments: list, deform: set[str], r: Report) -> None:
    import bpy

    bad_mod, bad_groups, bad_weights, bad_rigid, missing_meta = [], {}, {}, [], []
    for g in garments:
        if g["studio_binding"] == "skinned" and (not g.modifiers or g.modifiers[0].type != "ARMATURE"
                                                 or g.modifiers[0].object != parts.rig):
            bad_mod.append(g.name)
        names = {vg.index: vg.name for vg in g.vertex_groups}
        stray = sorted(n for n in names.values() if n not in deform)
        if stray:
            bad_groups[g.name] = stray
        unweighted = over = off = 0
        for v in g.data.vertices:
            ws = [e.weight for e in v.groups if names.get(e.group) in deform and e.weight > 0]
            if not ws:
                unweighted += 1
            elif len(ws) > 4:
                over += 1
            elif abs(sum(ws) - 1) > 0.02:
                off += 1
        if unweighted or over or off:
            bad_weights[g.name] = {"unweighted": unweighted, "over_4": over, "not_normalised": off}
        if g["studio_binding"] == "rigid":
            bone = g.get("studio_attach_bone")
            if bone not in deform or set(names.values()) != {bone} or g.parent_type != "BONE" \
                    or g.parent_bone != bone or any(m.type == "ARMATURE" for m in g.modifiers):
                bad_rigid.append(g.name)
        if not all(k in g for k in ("studio_slot", "studio_garment", "studio_layer", "studio_covers")):
            missing_meta.append(g.name)
    r.add("garment_armature_modifiers", not bad_mod, objects=bad_mod)
    r.add("garment_groups_are_deform_bones", not bad_groups, stray=bad_groups)
    r.add("garment_skin_weights", not bad_weights, problems=bad_weights)
    r.add("rigid_pieces_on_attachment_bones", not bad_rigid, objects=bad_rigid)
    r.add("garment_metadata", not missing_meta, objects=missing_meta)
    unparented = [g.name for g in garments if g.parent != parts.rig]
    mask = [m for m in parts.body.modifiers if m.name.startswith(MASK_PREFIX)]
    r.add("body_mask_modifier", len(mask) <= 1 and (not mask or parts.body.modifiers[0].type == "ARMATURE"),
          modifiers=[m.name for m in parts.body.modifiers])
    r.add("garments_parented_to_rig", not unparented, objects=unparented)
    mats = [g.name for g in garments if not g.data.materials or any(m is None for m in g.data.materials)]
    r.add("garment_materials", not mats, objects=mats)
    del bpy
