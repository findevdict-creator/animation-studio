"""The face track of an instance: expressions, automatic blinks and the dialogue mouth.

All of it is keys on the custom properties of the rig's face control bone, the same
properties the character's drivers read (``expr_happy`` 0..1 drives the happy brow, eyes and
mouth shape keys). The keys live in one scene-local Action on its own NLA track named
``face``; the character file is not touched.

Dialogue: a line with a recording (``AUDIO-NNN``) is lip-synced: each mouth cue found in the
audio by the lipsync job keys its viseme at the cue's time from the line's start frame
(1 for that viseme, 0 for the others, so the mouth eases between shapes). A line without a
recording falls back to a fixed viseme cycle between its frames.
"""

from __future__ import annotations

import hashlib

from studio_contracts.rig_profile import CARTOON_HUMANOID_V1, RigProfile
from studio_contracts.scene import NEUTRAL, ResolvedInstance

FLAP = ("vis_AI", "vis_O", "vis_E", "vis_MBP", "vis_U", "vis_AI", "vis_FV", "vis_O")
FLAP_STEP = 3  # frames per viseme
BLINK_EVERY_S = 3.4


def expression_values(expression: str, weight: float, profile: RigProfile = CARTOON_HUMANOID_V1) -> dict[str, float]:
    return {f"expr_{e}": (weight if e == expression else 0.0) for e in profile.expressions} if expression != NEUTRAL \
        else {f"expr_{e}": 0.0 for e in profile.expressions}


def blink_frames(key: str, start: int, end: int, fps: int) -> list[int]:
    """Deterministic per instance: the same manifest always blinks on the same frames."""
    offset = int(hashlib.sha256(key.encode()).hexdigest()[:4], 16) % max(int(fps * BLINK_EVERY_S), 1)
    step = int(fps * BLINK_EVERY_S)
    return list(range(start + fps // 2 + offset % fps, end - 3, step))


def build_face_track(rig, inst: ResolvedInstance, fps: int, start: int, end: int,
                     profile: RigProfile = CARTOON_HUMANOID_V1) -> dict:
    import bpy

    pb = rig.pose.bones[profile.face_control_bone]
    rig.animation_data_create()
    saved = rig.animation_data.action
    action = bpy.data.actions.new(f"{inst.key}:face")
    rig.animation_data.action = action

    def key(prop: str, value: float, frame: float) -> None:
        nonlocal count
        count += 1
        pb[prop] = float(value)
        pb.keyframe_insert(f'["{prop}"]', frame=frame, group="face")

    keyed: dict[str, list[list[float]]] = {}
    count = 0
    try:
        for e in sorted(inst.expressions, key=lambda e: e.frame):
            for prop, value in expression_values(e.expression, e.weight, profile).items():
                key(prop, value, e.frame)
            keyed.setdefault("expressions", []).append([e.frame, e.expression, e.weight])
        blinks = blink_frames(inst.key, start, end, fps) if inst.blink else []
        for f in blinks:
            for side in "LR":
                key(f"eye_blink_{side}", 0.0, f - 1)
                key(f"eye_blink_{side}", 1.0, f + 1)
                key(f"eye_blink_{side}", 0.0, f + 3)
        lines = []
        all_vis = [f"vis_{v}" for v in profile.visemes]
        for d in sorted(inst.dialogue, key=lambda d: d.start_frame):
            if getattr(d, "audio", None) is not None:
                cues = [c for c in d.audio.cues if d.start_frame + c.start_s * fps < d.end_frame]
                for c in cues:
                    f = d.start_frame + c.start_s * fps
                    for v in all_vis:
                        key(v, 1.0 if v == f"vis_{c.viseme}" else 0.0, f)
                for v in all_vis:
                    key(v, 1.0 if v == "vis_rest" else 0.0, d.end_frame)
                lines.append({"start_frame": d.start_frame, "end_frame": d.end_frame, "audio": d.audio.code,
                              "cues": len(cues), "analyser": d.audio.analyser})
                continue
            frames = list(range(d.start_frame, d.end_frame, FLAP_STEP))
            for i, f in enumerate(frames):
                for v in sorted(set(FLAP)):
                    key(v, 1.0 if v == FLAP[i % len(FLAP)] else 0.0, f)
            for v in sorted(set(FLAP)):
                key(v, 0.0, d.end_frame)
            key("vis_rest", 1.0, d.end_frame)
            key("vis_rest", 0.0, d.start_frame)
            lines.append({"start_frame": d.start_frame, "end_frame": d.end_frame, "visemes": len(frames)})
        for prop in [k for k in pb.keys() if k.startswith(("expr_", "vis_", "eye_"))]:
            pb[prop] = 0.0
    finally:
        rig.animation_data.action = saved
    if count:
        track = rig.animation_data.nla_tracks.new()
        track.name = "face"
        # a strip maps the action's own first frame to its start: keep absolute timing
        strip = track.strips.new("face", int(action.frame_range[0]), action)
        strip.blend_type = "REPLACE"
        strip.extrapolation = "HOLD"
    return {"action": action.name if count else None, "keys": count, "expressions": keyed.get("expressions", []), "blinks": blinks,
            "dialogue": lines}
