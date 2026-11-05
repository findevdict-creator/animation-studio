"""Retargeting a motion-capture take (BVH) onto the controls of a ``cartoon_humanoid@1.0`` rig.

Carried over from the clip pipeline of the first stack (``tools/retarget.py``, branch
``12-clip-pipeline``), which constrained an 11-bone placeholder rig to the source and baked
it. That approach copies world rotations, which is only right when both rigs share a rest
pose; this rig rests in an A pose and motion capture in a T pose. Here each frame is computed
directly:

1. the take is imported and turned so its left hip points along +X, like the rig (the
   character then faces -Y);
2. the **reference pose** of the source is its first frame (the cgspeed CMU conversion and
   Mixamo exports start in a T pose) or, failing that, its rest pose;
3. for every mapped control, the source bone's world rotation change from the reference
   pose is applied on top of an offset that turns the control from its rest direction onto
   the source bone's reference direction (limb controls; the spine controls are upright in
   both rest poses and get no offset), so the control points where the source bone points;
4. the hip bone's translation from the reference pose, scaled by leg length (target hip
   height over source hip height), moves the torso;
5. a travelling take has its average forward travel taken out of the keys and recorded as
   the stride, so a scene can move it at its own pace; the lowest foot is put back on the
   ground.

Controls are posed parent first in armature space and keyed on every output frame (24 fps,
resampled from the take's own rate). Arms and legs are switched to FK.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from studio_contracts.animation import FPS, BoneMap, ResolvedAnimation

from cartoon_engine.animation import rig as R

ORDER = ("torso", "chest", "neck", "head",
         *(f"{b}.{s}" for s in R.SIDES for b in ("upper_arm_fk", "forearm_fk", "hand_fk")),
         *(f"{b}.{s}" for s in R.SIDES for b in ("thigh_fk", "shin_fk", "foot_fk")))
ALL_FK = {"upper_arm_parent.L": 1.0, "upper_arm_parent.R": 1.0, "thigh_parent.L": 1.0, "thigh_parent.R": 1.0}


def import_bvh(path: Path):
    import bpy

    before = set(bpy.data.objects)
    bpy.ops.import_anim.bvh(filepath=str(path), global_scale=1.0, rotate_mode="NATIVE", axis_forward="-Z",
                            axis_up="Y", update_scene_fps=False, use_fps_scale=False, use_cyclic=False)
    new = [o for o in bpy.data.objects if o not in before]
    src = next((o for o in new if o.type == "ARMATURE"), None)
    if src is None or src.animation_data is None or src.animation_data.action is None:
        raise ValueError(f"{path.name}: no animated armature in the file")
    return src


def bvh_frame_time(path: Path) -> float:
    for line in Path(path).read_text(errors="replace").splitlines():
        if line.strip().lower().startswith("frame time:"):
            return float(line.split(":", 1)[1])
    raise ValueError(f"{path.name}: no 'Frame Time' line")


def _world(src, name: str):
    return src.matrix_world @ src.pose.bones[name].matrix


def _direction(src, name: str):
    """World direction of a source bone in the current frame: toward its first child's head
    (BVH end joints and zero-length joints get an arbitrary tail from the importer), else
    along its own tail, else its parent's direction."""
    pb = src.pose.bones[name]
    head = src.matrix_world @ pb.head
    for child in pb.children:
        d = (src.matrix_world @ child.head) - head
        if d.length > 1e-4:
            return d.normalized()
    d = (src.matrix_world @ pb.tail) - head
    if pb.bone.length > 1e-4 and not pb.children:
        return d.normalized()
    return _direction(src, pb.parent.name) if pb.parent else d.normalized()


def _face_plus_x(src, bm: BoneMap, scene, frame: int) -> None:
    """Turn the source about Z so its left hip is at +X of its right hip."""
    from mathutils import Matrix

    scene.frame_set(frame)
    left = next((b for b in bm.bones if bm.bones[b] == "thigh_fk.L"), None)
    right = next((b for b in bm.bones if bm.bones[b] == "thigh_fk.R"), None)
    if left is None or right is None:
        return
    d = _world(src, left).translation - _world(src, right).translation
    yaw = math.atan2(d.y, d.x)
    src.matrix_world = Matrix.Rotation(-yaw, 4, "Z") @ src.matrix_world
    scene.frame_set(frame)


def _hip_height(src, bm: BoneMap) -> float:
    feet = [b for b, c in bm.bones.items() if c.startswith("foot_fk")]
    hips = _world(src, bm.root).translation
    floor = min((_world(src, f).translation.z for f in feet), default=0.0)
    return float(hips.z - floor)


def retarget(rig, anim: ResolvedAnimation, path: Path, name: str):
    """Bake the take onto ``rig`` (at its rest placement); returns (action, info)."""
    import bpy
    from mathutils import Vector

    bm = anim.bone_map
    src_info = anim.bvh
    assert bm is not None and src_info is not None
    scene = bpy.context.scene
    src = import_bvh(path)
    try:
        missing = [b for b in bm.bones if b not in src.pose.bones]
        if missing:
            raise ValueError(f"the take has no bones {missing} of bone map {bm.id}")
        first, last = (int(round(v)) for v in src.animation_data.action.frame_range)
        ref_frame = first
        # the cgspeed / Mixamo T pose is the first frame; motion starts on the next one
        start = max(src_info.frame_start if src_info.frame_start is not None else first + 1, first)
        end = min(src_info.frame_end if src_info.frame_end is not None else last, last)
        if end <= start:
            raise ValueError(f"frames {start}..{end} are empty (the take has {first}..{last})")
        _face_plus_x(src, bm, scene, ref_frame)
        src_rate = 1.0 / bvh_frame_time(path)
        step = src_rate / FPS
        n = int(math.floor((end - start) / step))
        if n < 2:
            raise ValueError("the take is shorter than three frames at 24 fps")

        geo = R.leg_geometry(rig)
        scene.frame_set(ref_frame)
        scale = geo.leg_length / max(_hip_height(src, bm), 1e-6)
        ref = {b: _world(src, b) for b in bm.bones}
        hips0 = ref[bm.root].translation.copy()
        offsets: dict[str, object] = {}
        R.reset(rig, ORDER)
        for k, v in ALL_FK.items():
            R.set_switch(rig, k, v)
        R.update()
        rest = {c: rig.pose.bones[c].bone.matrix_local.to_quaternion() for c in ORDER}
        for b, c in bm.bones.items():
            if "_fk" in c:
                cb = rig.data.bones[c]
                c_dir = (cb.tail_local - cb.head_local).normalized()
                s_dir = _direction(src, b)
                offsets[c] = c_dir.rotation_difference(s_dir)
            else:
                from mathutils import Quaternion

                offsets[c] = Quaternion()
        by_control = {c: b for b, c in bm.bones.items()}
        controls = [c for c in ORDER if c in by_control]

        # sample the source once: world rotation deltas and hip travel per output frame
        samples = []
        for k in range(n + 1):
            t = start + k * step
            scene.frame_set(int(math.floor(t)), subframe=float(t - math.floor(t)))
            rot = {}
            for c in controls:
                b = by_control[c]
                rot[c] = _world(src, b).to_quaternion() @ ref[b].to_quaternion().inverted()
            samples.append((rot, (_world(src, bm.root).translation - hips0) * scale))
        travel = np.array([[s[1].x, s[1].y] for s in samples])
        forward = 0.0
        if src_info.travels:
            # keep the in-place part: take the straight line from first to last frame out
            trend = np.linspace(travel[0], travel[-1], n + 1)
            forward = float(-(travel[-1][1] - travel[0][1]))  # -Y is forward
            travel = travel - trend

        rig.animation_data_create()
        saved = rig.animation_data.action
        action = bpy.data.actions.new(name)
        rig.animation_data.action = action
        try:
            for k, (rot, hip) in enumerate(samples):
                R.reset(rig, controls)
                R.update()
                for c in controls:
                    pb = rig.pose.bones[c]
                    q = rot[c] @ offsets[c] @ rest[c]
                    m = q.to_matrix().to_4x4()
                    if c == "torso":
                        head = Vector(geo.torso_head) + Vector((travel[k][0], travel[k][1], hip.z))
                    else:
                        head = pb.head.copy()
                    m.translation = head
                    R.place(rig, c, m)
                for c in controls:
                    pb = rig.pose.bones[c]
                    pb.keyframe_insert("rotation_quaternion", frame=k, group=c)
                    if c == "torso":
                        pb.keyframe_insert("location", frame=k, group=c)
                for sw in ALL_FK:
                    rig.pose.bones[sw].keyframe_insert('["IK_FK"]', frame=k, group=sw)
        finally:
            R.reset(rig, controls)
            rig.animation_data.action = saved
            R.update()
        action.frame_range = (0.0, float(n))
        action.use_frame_range = True
        action.use_cyclic = anim.loops
        info = {"source_frames": [start, end], "source_fps": round(src_rate, 3), "frames": n, "scale": round(scale, 5),
                "mapped": {by_control[c]: c for c in controls}, "travel_m": round(forward, 4),
                "reference_frame": ref_frame}
        return action, info
    finally:
        arm = src.data
        bpy.data.objects.remove(src)
        bpy.data.armatures.remove(arm)
        for a in [a for a in bpy.data.actions if a.users == 0 and a.name.startswith(Path(path).stem)]:
            bpy.data.actions.remove(a)


def ground(rig, action, frames: int) -> float:
    """Shift the torso keys so the lowest foot point over the take is at its rest height;
    returns the shift in metres."""
    import bpy

    from cartoon_engine.animation.generators import fcurves
    from cartoon_engine.animation.measure import foot_tracks

    scene = bpy.context.scene
    rig.animation_data.action = action
    rig.animation_data.action_slot = action.slots[0]
    tracks = foot_tracks(rig, scene, list(range(frames + 1)))
    rest = min(rig.data.bones[f"DEF-toe.{s}"].head_local.z for s in R.SIDES)
    low = min(float(t[:, 2].min()) for k, t in tracks.items() if k.startswith("ball"))
    shift = rest - low
    # torso location is in its bone space, which is aligned with the armature
    for fc in fcurves(action):
        if fc.data_path == 'pose.bones["torso"].location' and fc.array_index == 2:
            for kp in fc.keyframe_points:
                kp.co[1] += shift
                kp.handle_left[1] += shift
                kp.handle_right[1] += shift
            fc.update()
    rig.animation_data.action = None
    return round(shift, 4)
