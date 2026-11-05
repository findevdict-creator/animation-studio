"""Assigning animations to an instance as NLA strips: library animations (``ANIM-*``,
Phase 6) and the built-in Phase 5 stand-in catalog (``CLIP-*@0.1``), kept so the scene
versions made with it still build.

Each clip is a few key poses on the rig's FK controls, made with the same pose helpers as
the fit check. A clip becomes one Action per instance (scene-local, never in a library
file); each assignment is a strip on its own NLA track, so assignments can overlap and
layer: clips on different controls add up, a later clip on the same control wins.
"""

from __future__ import annotations

from studio_contracts.rig_profile import CARTOON_HUMANOID_V1, RigProfile
from studio_contracts.scene import ResolvedClipUse, get_clip

from cartoon_engine.outfit.check import BACK, FWD, UP, _swing, _turn

X, Z = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)

# (frame at 24 fps, [(op, control, direction or axis, degrees)])
Key = tuple[int, list[tuple[str, str, tuple[float, float, float], float]]]
_WAVE_UP = [("swing", "upper_arm_fk.R", UP, 140.0)]
KEYS: dict[str, list[Key]] = {
    "idle": [(0, []), (24, [("turn", "chest", X, 3.0), ("turn", "head", X, -2.0)]), (48, [])],
    "wave": [(0, [*_WAVE_UP, ("swing", "forearm_fk.R", (1.0, 0.0, 0.0), 25.0)]),
             (9, [*_WAVE_UP, ("swing", "forearm_fk.R", (-1.0, 0.0, 0.0), 25.0)]),
             (18, [*_WAVE_UP, ("swing", "forearm_fk.R", (1.0, 0.0, 0.0), 25.0)]),
             (27, [*_WAVE_UP, ("swing", "forearm_fk.R", (-1.0, 0.0, 0.0), 25.0)]),
             (36, [*_WAVE_UP, ("swing", "forearm_fk.R", (1.0, 0.0, 0.0), 25.0)])],
    "walk": [(0, [("swing", "thigh_fk.L", FWD, 25.0), ("swing", "thigh_fk.R", BACK, 20.0),
                  ("swing", "shin_fk.R", BACK, 30.0), ("swing", "upper_arm_fk.L", BACK, 18.0),
                  ("swing", "upper_arm_fk.R", FWD, 18.0)]),
             (12, [("swing", "thigh_fk.R", FWD, 25.0), ("swing", "thigh_fk.L", BACK, 20.0),
                   ("swing", "shin_fk.L", BACK, 30.0), ("swing", "upper_arm_fk.R", BACK, 18.0),
                   ("swing", "upper_arm_fk.L", FWD, 18.0)]),
             (24, [("swing", "thigh_fk.L", FWD, 25.0), ("swing", "thigh_fk.R", BACK, 20.0),
                   ("swing", "shin_fk.R", BACK, 30.0), ("swing", "upper_arm_fk.L", BACK, 18.0),
                   ("swing", "upper_arm_fk.R", FWD, 18.0)])],
    "nod": [(0, []), (6, [("turn", "head", X, 15.0)]), (12, []), (18, [("turn", "head", X, 10.0)]), (24, [])],
    "point": [(0, []), (10, [("swing", "upper_arm_fk.L", FWD, 85.0)]), (30, [("swing", "upper_arm_fk.L", FWD, 85.0)])],
    "look_around": [(0, []), (12, [("turn", "head", Z, 35.0)]), (24, []), (36, [("turn", "head", Z, -35.0)]),
                    (48, [])],
}
FK_SWITCHES = ("upper_arm_parent.L", "upper_arm_parent.R", "thigh_parent.L", "thigh_parent.R")


def use_fk(rig) -> None:
    """The catalog poses FK controls: switch the limbs to FK on this instance's override."""
    for name in FK_SWITCHES:
        pb = rig.pose.bones.get(name)
        if pb is not None and "IK_FK" in pb:
            pb["IK_FK"] = 1.0


def _rest(rig, names) -> None:
    for n in names:
        pb = rig.pose.bones[n]
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)


def clip_action(rig, key: str, name: str, profile: RigProfile = CARTOON_HUMANOID_V1):
    """Bake catalog clip ``key`` into a new Action for ``rig`` (frames from 0 at 24 fps)."""
    import bpy

    clip = get_clip(key)
    controls = clip.controls
    rig.animation_data_create()
    saved = rig.animation_data.action
    action = bpy.data.actions.new(name)
    rig.animation_data.action = action
    try:
        for frame, ops in KEYS[clip.key]:
            _rest(rig, controls)
            bpy.context.view_layer.update()
            for op, bone, vec, deg in ops:
                (_swing if op == "swing" else _turn)(rig, bone, vec, deg)
            for bone in controls:
                rig.pose.bones[bone].keyframe_insert("rotation_quaternion", frame=frame, group=bone)
        _rest(rig, controls)
    finally:
        rig.animation_data.action = saved
    action["studio_clip"] = clip.ref
    return action


def assign(rig, uses: tuple[ResolvedClipUse, ...], fps: int, prefix: str, ws=None) -> list[dict]:
    """One NLA track per assignment, bottom to top in manifest order. Library animations
    (``ANIM-*``) are appended from their pinned file and fitted to this rig
    (``cartoon_engine.animation.apply``); a travelling one also moves the root."""
    from pathlib import Path

    from cartoon_engine.animation import apply as A

    rig.animation_data_create()
    actions: dict[str, object] = {}
    out = []
    travel: list[tuple[float, float, float]] = []
    for i, use in enumerate(uses):
        name = f"{i:02d}_{use.key}"
        if use.source == "library":
            if use.clip not in actions:
                actions[use.clip] = A.library_action(Path(ws), rig, use, f"{prefix}:{use.clip}")
            action, ratio, compat = actions[use.clip]
            track, strip = A.strip_for(rig, action, name, use, fps)
            info = {"track": track.name, "action": action.name, "clip": use.clip, "source": "library",
                    "frame_start": strip.frame_start, "frame_end": round(strip.frame_end, 3), "repeat": use.repeat,
                    "scale": round(strip.scale, 4), "leg_ratio": round(ratio, 4),
                    "action_source": action["studio_source"]}
            if compat.travels and use.travel and compat.stride_m > 0:
                metres = compat.stride_m * ratio * use.repeat
                travel.append((strip.frame_start, strip.frame_end, metres))
                info["travel_m"] = round(metres, 4)
            out.append(info)
            continue
        if use.key not in actions:
            actions[use.key] = clip_action(rig, use.key, f"{prefix}:{use.key}")
        action = actions[use.key]
        track = rig.animation_data.nla_tracks.new()
        track.name = name
        strip = track.strips.new(f"{use.key}", use.start_frame, action)
        strip.scale = (fps / 24.0) / use.speed
        strip.repeat = float(use.repeat)
        strip.blend_type = "REPLACE"
        strip.extrapolation = "NOTHING"
        if use.influence < 1.0:
            strip.use_animated_influence = True
            strip.influence = use.influence
        out.append({"track": track.name, "action": action.name, "clip": use.clip, "frame_start": strip.frame_start,
                    "frame_end": round(strip.frame_end, 3), "repeat": use.repeat, "scale": round(strip.scale, 4)})
    t = A.build_travel(rig, travel, f"{prefix}:travel")
    if t:
        out.append({"travel": t})
    return out
