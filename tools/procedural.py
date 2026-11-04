"""Procedural animation layers: code, not clips (asset build phase 2).

Breathing, blinking, look-at and feet-on-ground are added to a rigged character in the
scene by these functions; the stage 2 assembler calls them per shot so the Layout TD never
has to request an idle, blink or look clip. Every function is deterministic for its inputs
(blinks take a seed) and keys only what it owns.
"""

from __future__ import annotations

import math
import random
from typing import Any


def add_breathing(rig, bone: str, frame_start: int, frame_end: int, period_frames: int = 72, amplitude_deg: float = 1.5) -> int:
    """Gentle rotation of a chest/spine bone; returns the number of keyframes written."""
    if bone not in rig.pose.bones:
        return 0
    pb = rig.pose.bones[bone]
    pb.rotation_mode = "XYZ"
    count = 0
    for frame in range(frame_start, frame_end + 1, max(period_frames // 4, 1)):
        phase = 2 * math.pi * (frame - frame_start) / period_frames
        pb.rotation_euler = (math.radians(amplitude_deg) * math.sin(phase), 0.0, 0.0)
        pb.keyframe_insert("rotation_euler", frame=frame, group="procedural_breath")
        count += 1
    return count


def add_blinks(mesh, frame_start: int, frame_end: int, seed: int, keys: tuple[str, str] = ("blink_l", "blink_r"), mean_gap_sec: float = 4.0, fps: int = 24) -> list[int]:
    """Blinks on the eyelid shape keys at seeded random intervals; returns the blink frames."""
    shape_keys = mesh.data.shape_keys
    if not shape_keys:
        return []
    blocks = [shape_keys.key_blocks.get(k) for k in keys]
    blocks = [b for b in blocks if b is not None]
    if not blocks:
        return []
    rng = random.Random(seed)
    frames: list[int] = []
    frame = frame_start + int(rng.uniform(0.5, 2.0) * fps)
    while frame + 6 <= frame_end:
        for kb in blocks:
            kb.value = 0.0
            kb.keyframe_insert("value", frame=frame)
            kb.value = 1.0
            kb.keyframe_insert("value", frame=frame + 3)
            kb.value = 0.0
            kb.keyframe_insert("value", frame=frame + 6)
        frames.append(frame)
        frame += int(max(1.0, rng.gauss(mean_gap_sec, mean_gap_sec / 3)) * fps)
    return frames


def add_look_at(rig, bone: str, target, influence: float = 0.8, frame_start: int | None = None, frame_end: int | None = None):
    """A damped-track constraint on the head toward a target object; optional on/off keys."""
    if bone not in rig.pose.bones:
        return None
    pb = rig.pose.bones[bone]
    con = pb.constraints.new("DAMPED_TRACK")
    con.name = f"look_at_{target.name}"
    con.target = target
    con.track_axis = "TRACK_Y"
    con.influence = influence
    if frame_start is not None and frame_end is not None:
        con.influence = 0.0
        con.keyframe_insert("influence", frame=max(frame_start - 4, 1))
        con.influence = influence
        con.keyframe_insert("influence", frame=frame_start)
        con.keyframe_insert("influence", frame=frame_end)
        con.influence = 0.0
        con.keyframe_insert("influence", frame=frame_end + 4)
    return con


def add_feet_on_ground(rig, feet: list[str], ground_z: float = 0.0, offset_m: float = 0.0) -> list[Any]:
    """Keep feet from sinking below the ground with a limit on world Z."""
    cons = []
    for foot in feet:
        if foot not in rig.pose.bones:
            continue
        pb = rig.pose.bones[foot]
        con = pb.constraints.new("LIMIT_LOCATION")
        con.name = "feet_on_ground"
        con.owner_space = "WORLD"
        con.use_min_z = True
        con.min_z = ground_z + offset_m
        cons.append(con)
    return cons


def add_head_follow(rig, head_bone: str, spine_bone: str, factor: float = 0.3):
    """The head follows part of the spine's rotation so clips on the spine read on the head."""
    if head_bone not in rig.pose.bones or spine_bone not in rig.pose.bones:
        return None
    con = rig.pose.bones[head_bone].constraints.new("COPY_ROTATION")
    con.name = "head_follow"
    con.target = rig
    con.subtarget = spine_bone
    con.mix_mode = "ADD"
    con.owner_space = con.target_space = "LOCAL"
    con.influence = factor
    return con


PROCEDURAL_LAYERS: dict[str, dict[str, Any]] = {
    "breathe": {"tags": ["idle", "breathe"], "function": "add_breathing"},
    "blink": {"tags": ["blink"], "function": "add_blinks"},
    "look_at": {"tags": ["look_around", "look_at"], "function": "add_look_at"},
    "feet_on_ground": {"tags": [], "function": "add_feet_on_ground"},
    "head_follow": {"tags": [], "function": "add_head_follow"},
}
