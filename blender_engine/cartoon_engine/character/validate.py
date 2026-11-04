"""Character validation (SRS Phase 2 "character validation"): structure checks against the
rig profile plus motion checks that pose the rig and measure the evaluated meshes.

Works on a freshly built character or on a reopened ``.blend`` (``find_parts``), so the job
validates the file it saved, not only the objects it still holds in memory. Poses are
restored afterwards.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from studio_contracts.rig_profile import CARTOON_HUMANOID_V1, RigProfile

MATERIAL_SLOTS = ("skin", "hair", "brow", "eye_white", "iris", "pupil", "mouth", "teeth", "base_suit")


@dataclass
class Report:
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, **detail: Any) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), **detail})
        return ok

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks)

    def failed(self) -> list[str]:
        return [c["name"] for c in self.checks if not c["ok"]]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "failed": self.failed(), "checks": self.checks}


@dataclass
class Parts:
    rig: Any
    body: Any
    head: Any
    eyes: dict[str, Any]
    hair: Any | None

    def meshes(self) -> list:
        return [o for o in (self.body, self.head, self.eyes.get("L"), self.eyes.get("R"), self.hair) if o is not None]


def find_parts(collection=None) -> Parts:
    import bpy

    objs = collection.all_objects if collection is not None else bpy.data.objects
    # in a scene holding several characters Blender suffixes clashing names (body.001)
    by = {re.sub(r"\.\d{3}$", "", o.name): o for o in objs}
    rig = next(o for o in objs if o.type == "ARMATURE" and "studio_rig_profile" in o)
    return Parts(rig=rig, body=by["body"], head=by["head"], eyes={"L": by["eye.L"], "R": by["eye.R"]},
                 hair=by.get("hair"))


def _evaluated(obj) -> np.ndarray:
    import bpy

    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


def _group_indices(obj, group: str, min_weight: float = 0.5) -> np.ndarray:
    g = obj.vertex_groups.get(group)
    if g is None:
        return np.array([], dtype=int)
    gi = g.index
    return np.array([v.index for v in obj.data.vertices for e in v.groups if e.group == gi and e.weight >= min_weight],
                    dtype=int)


def _spine_only(obj) -> np.ndarray:
    """Vertices carried entirely by the lower spine (they must not move with an arm)."""
    names = {"DEF-spine", "DEF-spine.001", "DEF-spine.002"}
    idx = {g.index for g in obj.vertex_groups if g.name in names}
    return np.array([v.index for v in obj.data.vertices
                     if sum(e.weight for e in v.groups if e.group in idx) > 0.99], dtype=int)


# ------------------------------------------------------------- structure
def check_structure(p: Parts, height_m: float | None, profile: RigProfile, r: Report) -> None:
    bones = {b.name: b for b in p.rig.data.bones}
    missing_def = [b for b in profile.required_deform_bones if b not in bones or not bones[b].use_deform]
    r.add("deform_bones", not missing_def, missing=missing_def)
    missing_ctl = [b for b in (*profile.required_controls, *profile.extra_bones) if b not in bones]
    r.add("controls", not missing_ctl, missing=missing_ctl)
    stray = [b.name for b in p.rig.data.bones if b.use_deform and not b.name.startswith("DEF-")]
    r.add("deform_bones_prefixed", not stray, stray=stray)
    r.add("rig_profile_tag", p.rig.get("studio_rig_profile") == profile.ref, found=p.rig.get("studio_rig_profile"))

    keys = p.head.data.shape_keys
    names = [k.name for k in keys.key_blocks][1:] if keys else []
    r.add("shape_keys", names == profile.shape_keys(),
          missing=sorted(set(profile.shape_keys()) - set(names)), extra=sorted(set(names) - set(profile.shape_keys())))
    drivers = {d.data_path: d for d in (keys.animation_data.drivers if keys and keys.animation_data else [])}
    undriven = [n for n in profile.shape_keys() if f'key_blocks["{n}"].value' not in drivers]
    bad = [d.data_path for d in drivers.values() if not (d.driver.is_valid and d.driver.is_simple_expression)]
    r.add("shape_key_drivers", not undriven and not bad, undriven=undriven, not_simple=bad)
    ctrl = p.rig.pose.bones.get(profile.face_control_bone)
    props = [] if ctrl is None else [k for k in profile.face_properties() if k not in ctrl.keys()]
    r.add("face_control_properties", ctrl is not None and not props, missing=props)

    deform = {b.name for b in p.rig.data.bones if b.use_deform}
    problems: dict[str, Any] = {}
    for obj in p.meshes():
        allowed = {g.index for g in obj.vertex_groups if g.name in deform}
        unweighted = over = bad_sum = 0
        for v in obj.data.vertices:
            ws = [e.weight for e in v.groups if e.group in allowed and e.weight > 0]
            if not ws:
                unweighted += 1
            elif len(ws) > 4:
                over += 1
            elif abs(sum(ws) - 1.0) > 0.02:
                bad_sum += 1
        if unweighted or over or bad_sum:
            problems[obj.name] = {"unweighted": unweighted, "over_4": over, "not_normalised": bad_sum}
    r.add("skin_weights", not problems, problems=problems)
    empty_masks = [m for m in profile.mask_regions
                   if not any(_group_indices(o, f"mask_{m}").size for o in (p.body, p.head))]
    r.add("mask_groups", not empty_masks, empty=empty_masks)

    transforms = []
    for obj in [p.rig, *p.meshes()]:
        if any(abs(s - 1) > 1e-6 for s in obj.scale) or any(abs(a) > 1e-6 for a in obj.rotation_euler):
            transforms.append(obj.name)
    r.add("applied_transforms", not transforms, objects=transforms)
    mods = [o.name for o in p.meshes() if not o.modifiers or o.modifiers[0].type != "ARMATURE"
            or o.modifiers[0].object != p.rig]
    r.add("armature_modifiers", not mods, objects=mods)

    import bpy

    have = {m.name for m in bpy.data.materials}
    r.add("materials", set(MATERIAL_SLOTS) <= have, missing=sorted(set(MATERIAL_SLOTS) - have))

    rest = np.vstack([_evaluated(o) for o in (p.body, p.head)])
    top, bottom = float(rest[:, 2].max()), float(rest[:, 2].min())
    r.add("on_the_ground", abs(bottom) < 0.02 * top, bottom_m=round(bottom, 4))
    if height_m is not None:
        r.add("height", abs((top - bottom) - height_m) <= 0.02 * height_m, measured_m=round(top - bottom, 4),
              requested_m=height_m)
    xs = rest[:, 0]
    r.add("symmetric", abs(float(xs.max() + xs.min())) < 0.01 * top, offset_m=round(float(xs.max() + xs.min()), 4))


# ---------------------------------------------------------------- motion
def _update(p: Parts) -> None:
    """Re-evaluate after a pose or property change. Setting an ID property does not tag the
    depsgraph by itself, so the rig is tagged explicitly (keyframed properties tag it)."""
    import bpy

    p.rig.update_tag()
    bpy.context.view_layer.update()


def _reset_pose(p: Parts, profile: RigProfile) -> None:
    for pb in p.rig.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.scale = (1, 1, 1)
    ctrl = p.rig.pose.bones[profile.face_control_bone]
    for k in profile.face_properties():
        ctrl[k] = 0.0


def _rotate(pb, axis: tuple[float, float, float], degrees: float) -> None:
    from mathutils import Quaternion

    if pb.rotation_mode == "QUATERNION":
        pb.rotation_quaternion = Quaternion(axis, math.radians(degrees))
    else:
        e = [0.0, 0.0, 0.0]
        e[axis.index(1)] = math.radians(degrees)
        pb.rotation_euler = e


def check_motion(p: Parts, profile: RigProfile, r: Report) -> None:
    import bpy

    pb = p.rig.pose.bones
    _reset_pose(p, profile)
    _update(p)
    body0, head0 = _evaluated(p.body), _evaluated(p.head)
    eyeL0 = _evaluated(p.eyes["L"])
    height = float(body0[:, 2].max() - body0[:, 2].min()) or 1.0
    hand = _group_indices(p.body, "DEF-hand.L")
    chest = _spine_only(p.body)
    foot = _group_indices(p.body, "DEF-foot.L")
    try:
        # FK arm: raise the left upper arm
        if "IK_FK" in pb["upper_arm_parent.L"]:
            pb["upper_arm_parent.L"]["IK_FK"] = 1.0
        _rotate(pb["upper_arm_fk.L"], (0, 0, 1), 45.0)
        _update(p)
        body = _evaluated(p.body)
        hand_move = float(np.linalg.norm(body[hand] - body0[hand], axis=1).mean()) if hand.size else 0.0
        chest_move = float(np.linalg.norm(body[chest] - body0[chest], axis=1).max()) if chest.size else 1.0
        r.add("fk_arm_moves_hand_only", hand_move > 0.05 * height and chest_move < 1e-3,
              hand_mean_m=round(hand_move, 4), chest_max_m=round(chest_move, 5))
        r.add("no_explosion_fk", float(np.linalg.norm(body - body0, axis=1).max()) < height,
              max_m=round(float(np.linalg.norm(body - body0, axis=1).max()), 4))
        _reset_pose(p, profile)

        # IK leg: lift the left foot 10 cm
        lift = 0.1 * height
        foot_ik = pb["foot_ik.L"]
        _update(p)
        # move along world Z whatever the control's orientation
        mat = p.rig.matrix_world @ foot_ik.bone.matrix_local
        foot_ik.location = mat.to_3x3().inverted() @ __import__("mathutils").Vector((0.0, 0.0, lift))
        _update(p)
        body = _evaluated(p.body)
        dz = float((body[foot, 2] - body0[foot, 2]).mean()) if foot.size else 0.0
        r.add("ik_leg_lifts_foot", abs(dz - lift) < 0.35 * lift, lift_m=round(lift, 4), measured_m=round(dz, 4))
        _reset_pose(p, profile)

        # head turn carries head, eyes and hair
        _rotate(pb["head"], (0, 1, 0), 30.0)
        _update(p)
        head = _evaluated(p.head)
        eyeL = _evaluated(p.eyes["L"])
        r.add("head_turn", float(np.abs(head - head0).max()) > 0.02 * height
              and float(np.abs(eyeL - eyeL0).max()) > 0.01 * height,
              head_max_m=round(float(np.abs(head - head0).max()), 4))
        _reset_pose(p, profile)

        # facial controls: every face property moves the head mesh
        ctrl = pb[profile.face_control_bone]
        dead = []
        for prop in profile.face_properties():
            ctrl[prop] = 1.0
            _update(p)
            if float(np.abs(_evaluated(p.head) - head0).max()) < 1e-4:
                dead.append(prop)
            ctrl[prop] = 0.0
        r.add("face_controls_move_face", not dead, dead=dead)
        ctrl["expr_happy"] = 1.0
        ctrl["vis_AI"] = 1.0
        _update(p)
        blocks = p.head.data.shape_keys.evaluated_get(bpy.context.evaluated_depsgraph_get()).key_blocks
        r.add("visemes_override_mouth", blocks["expr_happy_mouth"].value < 1e-3 and blocks["expr_happy_brow"].value > 0.99,
              mouth=round(blocks["expr_happy_mouth"].value, 3))
        ctrl["expr_happy"] = ctrl["vis_AI"] = 0.0

        # eyes follow the target
        target = pb["eye_target"]
        target.location = (0.3, 0.0, 0.0)
        _update(p)
        eyeL = _evaluated(p.eyes["L"])
        r.add("eyes_track_target", float(np.abs(eyeL - eyeL0).max()) > 1e-3)
    finally:
        _reset_pose(p, profile)
        _update(p)


def validate(parts: Parts, height_m: float | None = None, profile: RigProfile = CARTOON_HUMANOID_V1) -> Report:
    r = Report()
    check_structure(parts, height_m, profile, r)
    check_motion(parts, profile, r)
    return r
