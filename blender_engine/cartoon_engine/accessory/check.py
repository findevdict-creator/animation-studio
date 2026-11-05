"""Attach check for accessories: the item sits on its bone and stays out of the body in
every pose of the suite (the same poses as the outfit fit check).

Per pose, every vertex of the item is tested against the posed skin near its attachment
point (``NEAR``: the head for glasses and hats, the forearm and hand for a watch, the hand
and legs for something held): the share of vertices inside and the deepest one are
compared with the style's thresholds. Far parts are left out on purpose: in the arms-up
pose a cartoon child's wrists meet its big head whatever it wears, and that is a property
of the character, not of the watch. Only skin faces count (not brows, lids or the mouth,
which are thin shells in front of the skin).
Structure checks: the item is parented to the attachment bone of the profile, its mesh is
linked from its library file (not copied) and it is not deformed by an Armature modifier.
"""

from __future__ import annotations

import numpy as np
from studio_contracts.accessory import AccessoryThresholds
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1, RigProfile

from cartoon_engine.character.validate import (
    Parts,
    Report,
    _evaluated,
    _reset_pose,
    _update,
)
from cartoon_engine.outfit.check import _masks, pose, set_fk
from cartoon_engine.outfit.garments import Surface, mesh_arrays


def _world(obj, co: np.ndarray) -> np.ndarray:
    m = np.array(obj.matrix_world)
    return co @ m[:3, :3].T + m[:3, 3]


#: mask regions an item on each attachment point is tested against
NEAR: dict[str, tuple[str, ...]] = {
    "face_eyes": ("head",), "head_top": ("head",), "back_center": ("torso",),
    "wrist_L": ("forearm", "hand"), "wrist_R": ("forearm", "hand"),
    "hand_L_grip": ("hand", "thigh", "shin"), "hand_R_grip": ("hand", "thigh", "shin"),
}


def _skin_polys(obj, regions: tuple[str, ...]) -> list:
    """Faces of ``obj`` with the skin material, inside the mask regions (head: all of it)."""
    _, polys = mesh_arrays(obj.data)
    skin = {i for i, m in enumerate(obj.data.materials) if m is not None and m.get("studio_slot", m.name) == "skin"}
    keep = np.array([p.material_index in skin for p in obj.data.polygons]) if skin else np.ones(len(polys), bool)
    if obj.name.split(".")[0] != "head":
        groups = {obj.vertex_groups[f"mask_{r}"].index for r in regions if f"mask_{r}" in obj.vertex_groups}
        member = np.array([any(e.group in groups and e.weight >= 0.5 for e in v.groups) for v in obj.data.vertices])
        keep &= np.array([member[list(p)].all() for p in polys])
    return [p for p, k in zip(polys, keep, strict=True) if k]


def inside(items: list, parts: Parts, reach: float = 0.05, polys_cache: dict | None = None) -> dict[str, dict]:
    """For the current pose: per item, how many of its vertices are inside the skin near
    its attachment point, and how deep."""
    from mathutils import Vector

    cache: dict[tuple, list] = {}
    polys_cache = {} if polys_cache is None else polys_cache
    out = {}
    for obj in items:
        regions = NEAR.get(obj.get("studio_accessory_point", ""), ("head", "torso"))
        if regions not in cache:
            skins = []
            for skin_obj in (parts.body, parts.head):
                if skin_obj is parts.head and "head" not in regions:
                    continue
                key = (skin_obj.name, regions)
                if key not in polys_cache:
                    polys_cache[key] = _skin_polys(skin_obj, regions)
                polys = polys_cache[key]
                if polys:
                    surf = Surface(_world(skin_obj, _evaluated(skin_obj)), polys)
                    surf.center = Vector(surf.points.mean(axis=0)) if skin_obj is parts.head else None
                    skins.append(surf)
            cache[regions] = skins
        skins = cache[regions]
        co, _ = mesh_arrays(obj.data)
        pts = _world(obj, co)
        depth = np.zeros(len(pts))
        for i, p in enumerate(pts):
            v = Vector(p)
            for s in skins:
                r = s.signed(v, reach)
                if r is None or r[0] >= 0:
                    continue
                if s.center is not None:
                    # the stand-in head has a few faces wound inwards; for a closed, roundish
                    # surface the outside is away from its centre whatever the winding
                    loc = v - r[1] * r[0]
                    if (loc - s.center).dot(r[1]) < 0:
                        continue
                depth[i] = max(depth[i], -r[0])
        n_in = int((depth > 0.0005).sum())
        out[obj.name] = {"vertices": len(pts), "inside": n_in, "inside_share": round(n_in / max(len(pts), 1), 4),
                         "inside_depth_m": round(float(depth.max()), 4)}
    return out


def attach_check(parts: Parts, attached: list, thresholds: AccessoryThresholds, poses: tuple[str, ...],
                 profile: RigProfile = CARTOON_HUMANOID_V1) -> tuple[Report, dict]:
    """``attached``: :class:`cartoon_engine.accessory.attach.Attached` items on ``parts``."""
    r = Report()
    for a in attached:
        r.add(f"on_attachment_bone:{a.obj.name}", a.obj.parent == parts.rig and a.obj.parent_type == "BONE"
              and a.obj.parent_bone == profile.attachment_points[a.point], bone=a.obj.parent_bone, point=a.point)
        r.add(f"rigid:{a.obj.name}", not any(m.type == "ARMATURE" for m in a.obj.modifiers))
    per_pose: dict[str, dict] = {a.obj.name: {} for a in attached}
    switches = set_fk(parts, True)
    _masks(parts, False)  # the skin is tested whole, also where an outfit hides it
    polys_cache: dict = {}
    try:
        for name in poses:
            pose(parts, name, profile)
            for obj_name, e in inside([a.obj for a in attached], parts, polys_cache=polys_cache).items():
                per_pose[obj_name][name] = e
    finally:
        _reset_pose(parts, profile)
        _masks(parts, True)
        set_fk(parts, switches)
        _update(parts)
    for a in attached:
        entries = per_pose[a.obj.name]
        bad = sorted(p for p, e in entries.items() if e["inside_share"] > thresholds.max_inside_share
                     or e["inside_depth_m"] > thresholds.max_inside_depth_m)
        r.add(f"clear_of_body:{a.obj.name}", not bad, poses=bad,
              worst_share=max((e["inside_share"] for e in entries.values()), default=0.0),
              worst_depth_m=max((e["inside_depth_m"] for e in entries.values()), default=0.0))
    return r, per_pose
