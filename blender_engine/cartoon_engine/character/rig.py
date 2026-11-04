"""Rig standard ``cartoon_humanoid@1.0`` on the stand-in template (architecture 8).

1. Rigify's human metarig, without the face rig, breasts and pelvis helpers, fitted to the
   skeleton landmarks (bone rolls keep the metarig's axes, so FK and finger curls bend the
   way Rigify expects) and generated headless.
2. Extra bones: ``DEF-eye.L/R`` (deform, damped-track to ``eye_target``) and ``face_ctrl``
   whose custom properties drive every facial shape key through simple-expression drivers
   (they evaluate with auto-run scripts disabled, as every studio job runs).
3. Deterministic distance-based skin weights to the DEF bones (at most four per vertex,
   normalised), the head and hair rigid to ``DEF-spine.006``, ``mask_<region>`` groups for
   outfit masking.
"""

from __future__ import annotations

import numpy as np

from studio_contracts.rig_profile import CARTOON_HUMANOID_V1, RigProfile

from cartoon_engine.character.build import Character
from cartoon_engine.character.skeleton import FINGERS, Skeleton, segments

REMOVE_SUBTREES = ("face", "breast.L", "breast.R", "pelvis.L", "pelvis.R")
#: Rigify splits these limb bones into two deform segments (name, name.001)
SPLIT = ("DEF-upper_arm", "DEF-forearm", "DEF-thigh", "DEF-shin")
MAX_INFLUENCES = 4


def _metarig_targets(sk: Skeleton) -> dict[str, tuple[tuple, tuple]]:
    J = sk.joints
    t: dict[str, tuple[tuple, tuple]] = {
        "spine": (J["pelvis"], J["spine1"]), "spine.001": (J["spine1"], J["spine2"]),
        "spine.002": (J["spine2"], J["chest"]), "spine.003": (J["chest"], J["neck_base"]),
        "spine.004": (J["neck_base"], J["neck_mid"]), "spine.005": (J["neck_mid"], J["head_base"]),
        "spine.006": (J["head_base"], J["head_top"]),
    }
    for s in "LR":
        g = lambda n: J[f"{n}.{s}"]  # noqa: E731
        palm_end = tuple(2 * a - b for a, b in zip(g("palm"), g("wrist")))
        t[f"shoulder.{s}"] = (g("clavicle"), g("shoulder"))
        t[f"upper_arm.{s}"] = (g("shoulder"), g("elbow"))
        t[f"forearm.{s}"] = (g("elbow"), g("wrist"))
        t[f"hand.{s}"] = (g("wrist"), palm_end)
        for i, f in enumerate(("index", "middle", "ring", "pinky"), start=1):
            base = tuple(a + (b - a) * 0.25 for a, b in zip(g("wrist"), g(f"{f}_0")))
            t[f"palm.0{i}.{s}"] = (base, g(f"{f}_0"))
            for k in (1, 2, 3):
                t[f"f_{f}.0{k}.{s}"] = (g(f"{f}_{k - 1}"), g(f"{f}_{k}"))
        for k in (1, 2, 3):
            t[f"thumb.0{k}.{s}"] = (g(f"thumb_{k - 1}"), g(f"thumb_{k}"))
        t[f"thigh.{s}"] = (g("hip"), g("knee"))
        t[f"shin.{s}"] = (g("knee"), g("ankle"))
        t[f"foot.{s}"] = (g("ankle"), g("ball"))
        t[f"toe.{s}"] = (g("ball"), g("toe"))
        heel = g("heel")
        half = 0.35 * max(sk.radii[f"ankle.{s}"])
        t[f"heel.02.{s}"] = ((heel[0] - half, heel[1], 0.0), (heel[0] + half, heel[1], 0.0))
    return t


def _subtree(bone) -> list:
    out = [bone]
    for c in bone.children:
        out.extend(_subtree(c))
    return out


def build_metarig(sk: Skeleton):
    import addon_utils
    import bpy

    addon_utils.enable("rigify", default_set=True, persistent=False)
    bpy.ops.object.armature_human_metarig_add()
    meta = bpy.context.active_object
    meta.name = "metarig"
    bpy.ops.object.mode_set(mode="EDIT")
    eb = meta.data.edit_bones
    for root in REMOVE_SUBTREES:
        if root in eb:
            for b in reversed(_subtree(eb[root])):
                eb.remove(b)
    targets = _metarig_targets(sk)
    axes = {b.name: b.z_axis.copy() for b in eb}
    connected = {b.name: b.use_connect for b in eb}
    missing = sorted(set(targets) - set(eb.keys()))
    if missing:
        raise RuntimeError(f"metarig lacks bones {missing}")
    order = sorted(eb, key=lambda b: len(b.parent_recursive))
    for b in order:
        if b.name not in targets:
            continue
        head, tail = targets[b.name]
        b.use_connect = False
        b.head, b.tail = head, tail
    for b in order:
        if b.name in targets:
            b.align_roll(axes[b.name])
    # restore the metarig's connections; the targets put connected heads on parent tails
    for b in order:
        if connected.get(b.name) and b.parent is not None:
            if (b.head - b.parent.tail).length > 1e-5:
                raise RuntimeError(f"{b.name} must start where {b.parent.name} ends")
            b.use_connect = True
    extra = sorted(set(eb.keys()) - set(targets))
    bpy.ops.object.mode_set(mode="OBJECT")
    if extra:
        raise RuntimeError(f"metarig has bones the template does not place: {extra}")
    return meta


def generate(meta):
    import bpy

    bpy.context.view_layer.objects.active = meta
    meta.select_set(True)
    before = {o.name for o in bpy.data.objects}
    bpy.ops.pose.rigify_generate()
    rig = next(o for o in bpy.data.objects if o.name not in before and o.type == "ARMATURE")
    rig.name = "rig"
    rig.data.name = "rig"
    bpy.data.objects.remove(meta)
    return rig


def add_face_bones(rig, ch: Character, profile: RigProfile = CARTOON_HUMANOID_V1) -> None:
    import bpy
    from mathutils import Vector

    lay = ch.head_parts.layout
    sk = ch.skeleton
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    eb = rig.data.edit_bones
    head_ctrl = eb["head"]
    head_def = eb[profile.head_deform_bone]
    r = lay.eye_r
    for side in "LR":
        c = Vector(lay.eye_center[side])
        b = eb.new(f"DEF-eye.{side}")
        b.head, b.tail = c, c + Vector((0.0, -r * 1.5, 0.0))
        b.roll = 0.0
        b.parent = head_def
        b.use_deform = True
    target = eb.new("eye_target")
    mid = (Vector(lay.eye_center["L"]) + Vector(lay.eye_center["R"])) / 2
    target.head = mid + Vector((0.0, -sk.head_h * 1.6, 0.0))
    target.tail = target.head + Vector((0.0, 0.0, r * 2))
    target.parent = head_ctrl
    target.use_deform = False
    face = eb.new(profile.face_control_bone)
    top = Vector(sk.j("head_top"))
    face.head = top + Vector((sk.head_w * 0.65, 0.0, 0.0))
    face.tail = face.head + Vector((0.0, 0.0, sk.head_h * 0.25))
    face.parent = head_ctrl
    face.use_deform = False
    bpy.ops.object.mode_set(mode="OBJECT")

    coll = rig.data.collections.get("Face") or rig.data.collections.new("Face")
    for name in ("eye_target", profile.face_control_bone):
        coll.assign(rig.data.bones[name])
    defc = rig.data.collections.get("DEF")
    for side in "LR":
        if defc is not None:
            defc.assign(rig.data.bones[f"DEF-eye.{side}"])
        pb = rig.pose.bones[f"DEF-eye.{side}"]
        con = pb.constraints.new("DAMPED_TRACK")
        con.target = rig
        con.subtarget = "eye_target"
        con.track_axis = "TRACK_Y"
    for name in ("eye_target", profile.face_control_bone):
        rig.pose.bones[name].rotation_mode = profile.rotation_mode


def add_face_controls(rig, head_obj, profile: RigProfile = CARTOON_HUMANOID_V1) -> dict[str, str]:
    """Custom properties on the face control bone and one driver per shape key. Returns
    shape key -> driver expression."""
    pb = rig.pose.bones[profile.face_control_bone]
    for prop in profile.face_properties():
        pb[prop] = 0.0
        ui = pb.id_properties_ui(prop)
        ui.update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, default=0.0)
        pb.property_overridable_library_set(f'["{prop}"]', True)

    keys = head_obj.data.shape_keys
    visemes = [f"vis_{v}" for v in profile.visemes]
    expressions = {}
    for kb in keys.key_blocks[1:]:
        name = kb.name
        fc = kb.driver_add("value")
        drv = fc.driver
        drv.type = "SCRIPTED"
        if name.startswith("expr_"):
            prop = "_".join(name.split("_")[:2])  # expr_happy_mouth -> expr_happy
        else:
            prop = name
        _var(drv, "p", rig, f'pose.bones["{profile.face_control_bone}"]["{prop}"]')
        if name.startswith("expr_") and name.endswith("_mouth"):
            # visemes win over the mouth part of an expression while speaking
            names = []
            for i, vis in enumerate(visemes):
                _var(drv, f"v{i}", rig, f'pose.bones["{profile.face_control_bone}"]["{vis}"]')
                names.append(f"v{i}")
            drv.expression = f"p * (1 - min(1, {' + '.join(names)}))"
        else:
            drv.expression = "p"
        # Blender adds a modifier-free F-curve; the driver output is the value directly
        for mod in list(fc.modifiers):
            fc.modifiers.remove(mod)
        expressions[name] = drv.expression
    return expressions


def _var(drv, name: str, rig, path: str) -> None:
    v = drv.variables.new()
    v.name = name
    v.type = "SINGLE_PROP"
    v.targets[0].id_type = "OBJECT"
    v.targets[0].id = rig
    v.targets[0].data_path = path


# ------------------------------------------------------------------ weights
def _seg_arrays(sk: Skeleton):
    segs = segments(sk)
    a = np.array([sk.j(s[2]) for s in segs])
    b = np.array([sk.j(s[3]) for s in segs])

    def rad(j):
        r = sk.radii.get(j)
        return max(r) if r else 0.02 * sk.height

    ra = np.array([rad(s[2]) for s in segs])
    rb = np.array([rad(s[3]) for s in segs])
    return segs, a, b, ra, rb


def body_weights(sk: Skeleton, coords: np.ndarray, rig_bones: set[str]):
    """Per vertex: up to MAX_INFLUENCES (bone, weight) pairs and the mask region."""
    segs, a, b, ra, rb = _seg_arrays(sk)
    ab = b - a
    denom = np.maximum((ab ** 2).sum(axis=1), 1e-12)
    rel = coords[:, None, :] - a[None, :, :]
    t = np.clip((rel * ab[None]).sum(axis=2) / denom[None], 0.0, 1.0)
    closest = a[None] + t[..., None] * ab[None]
    d = np.linalg.norm(coords[:, None, :] - closest, axis=2)
    r = ra[None] * (1 - t) + rb[None] * t
    nd = d / np.maximum(r, 1e-4)
    order = np.argsort(nd, axis=1)[:, :3]
    out_weights: list[dict[str, float]] = []
    regions: list[str] = []
    for vi in range(coords.shape[0]):
        idx = order[vi]
        best = nd[vi, idx[0]]
        w: dict[str, float] = {}
        for j in idx:
            influence = 1.0 / (max(nd[vi, j], 1e-3) ** 6)
            if nd[vi, j] > best * 1.6 + 0.3:  # only blend with segments nearly as close
                continue
            bone = segs[j][0]
            tt = float(t[vi, j])
            if bone in SPLIT_BONES and f"{bone}.001" in rig_bones:
                s = min(1.0, max(0.0, (tt - 0.3) / 0.4))
                s = s * s * (3 - 2 * s)
                w[bone] = w.get(bone, 0.0) + influence * (1 - s)
                w[f"{bone}.001"] = w.get(f"{bone}.001", 0.0) + influence * s
            else:
                w[bone] = w.get(bone, 0.0) + influence
        top = sorted(w.items(), key=lambda kv: -kv[1])[:MAX_INFLUENCES]
        total = sum(v for _, v in top)
        out_weights.append({k: v / total for k, v in top if v / total > 0.01})
        regions.append(segs[idx[0]][1])
    return out_weights, regions


SPLIT_BONES = {f"{b}.{s}" for b in SPLIT for s in "LR"}


def skin(ch: Character, rig, profile: RigProfile = CARTOON_HUMANOID_V1) -> dict[str, int]:
    bones = {b.name for b in rig.data.bones}
    stats = {}
    mesh = ch.body.data
    coords = np.array([v.co[:] for v in mesh.vertices])
    weights, regions = body_weights(ch.skeleton, coords, bones)
    missing = {b for w in weights for b in w} - bones
    if missing:
        raise RuntimeError(f"weights name bones the rig lacks: {sorted(missing)}")
    groups: dict[str, object] = {}
    by_group: dict[str, dict[float, list[int]]] = {}
    for vi, w in enumerate(weights):
        for bone, val in w.items():
            by_group.setdefault(bone, {}).setdefault(round(val, 4), []).append(vi)
    for bone, buckets in by_group.items():
        g = groups.setdefault(bone, ch.body.vertex_groups.new(name=bone))
        for val, idx in buckets.items():
            g.add(idx, val, "REPLACE")
    for region in profile.mask_regions:
        idx = [i for i, r in enumerate(regions) if r == region]
        if idx:
            ch.body.vertex_groups.new(name=f"mask_{region}").add(idx, 1.0, "REPLACE")
    stats["body_vertices"] = len(weights)
    stats["body_groups"] = len(by_group)

    rigid = [(ch.head, profile.head_deform_bone), (ch.eyes["L"], "DEF-eye.L"), (ch.eyes["R"], "DEF-eye.R")]
    if ch.hair is not None:
        rigid.append((ch.hair, profile.head_deform_bone))
    for obj, bone in rigid:
        obj.vertex_groups.new(name=bone).add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    ch.head.vertex_groups.new(name="mask_head").add(list(range(len(ch.head.data.vertices))), 1.0, "REPLACE")

    for obj in ch.meshes():
        mod = obj.modifiers.new("rig", "ARMATURE")
        mod.object = rig
        mod.use_deform_preserve_volume = obj is ch.body
        while obj.modifiers[0] != mod:  # deform before subdivision
            obj.modifiers.move(obj.modifiers.find(mod.name), 0)
        obj.parent = rig
        obj.matrix_parent_inverse.identity()
    return stats


def build_rig(ch: Character, profile: RigProfile = CARTOON_HUMANOID_V1) -> dict:
    import bpy

    meta = build_metarig(ch.skeleton)
    rig = generate(meta)
    add_face_bones(rig, ch, profile)
    drivers = add_face_controls(rig, ch.head, profile)
    stats = skin(ch, rig, profile)
    for c in list(rig.users_collection):
        c.objects.unlink(rig)
    ch.collection.objects.link(rig)
    # Rigify's widget collection: keep it with the character, hidden
    for coll in bpy.data.collections:
        if coll.name.startswith("WGTS"):
            for parent in list(bpy.context.scene.collection.children):
                if parent == coll:
                    bpy.context.scene.collection.children.unlink(coll)
            if coll.name not in ch.collection.children:
                ch.collection.children.link(coll)
            coll.hide_render = True
            coll.hide_viewport = True
    rig["studio_rig_profile"] = profile.ref
    rig["studio_attachment_points"] = dict(profile.attachment_points)
    ch.rig = rig
    return {"drivers": len(drivers), **stats, "bones": len(rig.data.bones)}
