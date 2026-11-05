"""Putting a library animation on a character instance in a scene (rig compatibility at
play time).

``animation.blend`` holds one Action (``ANIM_<key>``) keyed on rig-profile controls and a
stamp of its compatibility record. For an instance:

1. the Action is appended (each instance gets its own copy; the library file is unchanged
   and the copy is stamped with its source ``ANIM-003@1.0#sha256``);
2. its location keys (torso, IK feet) are scaled by the wearer's leg length over the
   reference body's, so a planted foot stays planted on a taller or shorter character;
   rotation keys are proportion-free and kept as they are;
3. it becomes an NLA strip on its own track, like the stand-in clips, with short blends in
   and out so clips hand over without a pop;
4. a travelling animation (a walk) also moves the instance's ``root`` control forward at the
   speed its feet imply (stride x leg-length ratio per cycle) for as long as it plays: one
   ``travel`` Action for the instance on its own track (no clip keys the root).
"""

from __future__ import annotations

from pathlib import Path

from studio_contracts.animation import FPS, AnimationCompat
from studio_contracts.scene import ResolvedClipUse

from cartoon_engine.animation import rig as R
from cartoon_engine.animation.generators import fcurves

ACTION_PREFIX = "ANIM_"
LOCATION_CONTROLS = ("torso", "foot_ik.L", "foot_ik.R", "root")


def animation_file_contents(path: Path) -> dict:
    import bpy

    with bpy.data.libraries.load(str(path), link=True) as (src, _):
        return {"actions": list(src.actions), "objects": list(src.objects), "armatures": list(src.armatures),
                "meshes": list(src.meshes), "collections": list(src.collections)}


def file_is_alone(contents: dict) -> bool:
    """An animation file holds one ANIM_ Action and nothing else (no rig, no character)."""
    return (len(contents["actions"]) == 1 and contents["actions"][0].startswith(ACTION_PREFIX)
            and not contents["objects"] and not contents["armatures"] and not contents["meshes"])


def append_action(path: Path, name: str):
    import bpy

    with bpy.data.libraries.load(str(Path(path).resolve()), link=False) as (src, dst):
        names = [a for a in src.actions if a.startswith(ACTION_PREFIX)]
        if len(names) != 1:
            raise ValueError(f"{path} has {len(names)} ANIM_ actions, expected 1")
        dst.actions = names
    action = dst.actions[0]
    action.name = name
    return action


def scale_locations(action, ratio: float) -> int:
    n = 0
    for fc in fcurves(action):
        if fc.data_path.endswith(".location") and any(f'"{c}"' in fc.data_path for c in LOCATION_CONTROLS):
            for kp in fc.keyframe_points:
                kp.co[1] *= ratio
                kp.handle_left[1] *= ratio
                kp.handle_right[1] *= ratio
            fc.update()
            n += 1
    return n


def strip_for(rig, action, track_name: str, use: ResolvedClipUse, fps: int):
    track = rig.animation_data.nla_tracks.new()
    track.name = track_name
    strip = track.strips.new(track_name, use.start_frame, action)
    if hasattr(strip, "action_slot") and action.slots:
        strip.action_slot = action.slots[0]
    strip.scale = (fps / FPS) / use.speed
    strip.repeat = float(use.repeat)
    strip.blend_type = "REPLACE"
    strip.extrapolation = "NOTHING"
    length = strip.frame_end - strip.frame_start
    blend = min(6.0, length / 4)
    strip.blend_in = strip.blend_out = blend
    if use.influence < 1.0:
        strip.use_animated_influence = True
        strip.influence = use.influence
    return track, strip


def wearer_ratio(rig, compat: AnimationCompat) -> float:
    return R.leg_geometry(rig).leg_length / compat.reference_leg_length_m


def library_action(ws: Path, rig, use: ResolvedClipUse, name: str):
    """Append and fit the pinned animation to this rig; returns (action, ratio, compat)."""
    assert use.pin is not None and use.compat is not None
    compat = AnimationCompat.model_validate(use.compat)
    action = append_action(ws / use.pin.path / "animation.blend", name)
    ratio = wearer_ratio(rig, compat)
    scale_locations(action, ratio)
    action["studio_source"] = f"{use.pin.ref}#{use.pin.files['animation.blend']}"
    action["studio_scale"] = ratio
    return action, ratio, compat


def build_travel(rig, segments: list[tuple[float, float, float]], name: str) -> dict | None:
    """``segments``: (start frame, end frame, metres) in scene frames. Keys the root's
    forward travel (-Y of the rig), holding still between segments."""
    import bpy

    if not segments:
        return None
    from mathutils import Vector

    segments = sorted(segments)
    action = bpy.data.actions.new(name)
    saved = rig.animation_data.action
    rig.animation_data.action = action
    pb = rig.pose.bones["root"]
    pos = 0.0
    keys = []
    try:
        pb.location = (0.0, 0.0, 0.0)
        first = segments[0][0]
        pb.keyframe_insert("location", frame=first, group="root")
        for start, end, metres in segments:
            pb.location = Vector((0.0, -pos, 0.0))
            pb.keyframe_insert("location", frame=start, group="root")
            pos += metres
            pb.location = Vector((0.0, -pos, 0.0))
            pb.keyframe_insert("location", frame=end, group="root")
            keys.append([round(start, 3), round(end, 3), round(metres, 4)])
        for fc in fcurves(action):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"
    finally:
        pb.location = (0.0, 0.0, 0.0)
        rig.animation_data.action = saved
    track = rig.animation_data.nla_tracks.new()
    track.name = "travel"
    strip = track.strips.new("travel", int(first), action)
    if hasattr(strip, "action_slot") and action.slots:
        strip.action_slot = action.slots[0]
    strip.blend_type = "REPLACE"
    strip.extrapolation = "HOLD"
    return {"action": action.name, "segments": keys, "distance_m": round(pos, 4)}
