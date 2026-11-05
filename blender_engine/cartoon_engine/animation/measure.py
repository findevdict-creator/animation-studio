"""Measuring an animation on a rig, frame by frame, on the evaluated deform bones.

Used by the build check of an animation version (on the reference body, and on every other
reference body it is declared for) and by the reopen check of a scene (on each instance, in
the world, with the scene's own travel).

* **Foot slide**: a foot point (ankle, ball, toe tip) is planted while it is within
  ``CONTACT_M`` of the lowest height it reaches in the clip; the worst horizontal drift over
  a run of planted frames is the slide.
* **Stretch**: thigh plus shin length posed over rest; the IK legs stretch when asked to
  reach too far, which reads as rubber legs.
* **Ground**: the lowest foot point; below ``-SINK_M`` the foot goes through the floor.
* **Loop**: for a looping clip, the largest rotation between the first and last frame of
  any keyed control.
"""

from __future__ import annotations

import math

import numpy as np

CONTACT_M = 0.004
SINK_M = 0.01
POINTS = (("ankle", "DEF-foot", "head"), ("ball", "DEF-toe", "head"), ("tip", "DEF-toe", "tail"))


def _set_frame(scene, f: float) -> None:
    scene.frame_set(int(math.floor(f)), subframe=float(f - math.floor(f)))


def foot_tracks(rig, scene, frames: list[float], travel=None) -> dict[str, np.ndarray]:
    """World positions (frames x 3) of each foot point; ``travel(f)`` is added when given
    (an in-place clip measured as if the scene moved it)."""
    out: dict[str, list] = {}
    for f in frames:
        _set_frame(scene, f)
        m = rig.matrix_world
        extra = np.array(travel(f)) if travel else np.zeros(3)
        for s in "LR":
            for name, bone, end in POINTS:
                pb = rig.pose.bones[f"{bone}.{s}"]
                p = np.array(m @ (pb.head if end == "head" else pb.tail)) + extra
                out.setdefault(f"{name}.{s}", []).append(p)
    return {k: np.array(v) for k, v in out.items()}


def slide_cm(track: np.ndarray, contact_m: float = CONTACT_M, floor: float | None = None) -> float:
    """``floor``: the lowest height of the point over the whole clip, when ``track`` covers
    only part of it (a scene that ends mid-strip); the track's own lowest otherwise."""
    floor = track[:, 2].min() if floor is None else floor
    planted = track[:, 2] <= floor + contact_m
    worst = run = 0.0
    for i in range(1, len(track)):
        if planted[i] and planted[i - 1]:
            run += float(np.hypot(*(track[i, :2] - track[i - 1, :2])))
            worst = max(worst, run)
        else:
            run = 0.0
    return round(worst * 100.0, 2)


def stride_from_tracks(tracks: dict[str, np.ndarray], frames: int) -> float:
    """Forward travel per cycle implied by the planted ball of each foot moving backwards
    under an in-place clip (armature -Y is forward); 0 for a clip whose feet do not move."""
    speeds = []
    for s in "LR":
        t = tracks[f"ball.{s}"]
        planted = t[:, 2] <= t[:, 2].min() + CONTACT_M
        idx = np.nonzero(planted[1:] & planted[:-1])[0]
        if len(idx) >= 2:
            speeds.append(float(np.mean(t[idx + 1, 1] - t[idx, 1])))
    if not speeds:
        return 0.0
    v = float(np.mean(speeds))  # +Y per frame while planted = forward speed of the body
    return round(max(v, 0.0) * frames, 4)


def leg_stretch(rig, scene, frames: list[float]) -> float:
    worst = 0.0
    for f in frames:
        _set_frame(scene, f)
        for s in "LR":
            posed = sum((rig.pose.bones[f"DEF-{b}.{s}"].tail - rig.pose.bones[f"DEF-{b}.{s}"].head).length
                        for b in ("thigh", "thigh.001", "shin", "shin.001") if f"DEF-{b}.{s}" in rig.pose.bones)
            rest = sum(rig.data.bones[f"DEF-{b}.{s}"].length
                       for b in ("thigh", "thigh.001", "shin", "shin.001") if f"DEF-{b}.{s}" in rig.data.bones)
            worst = max(worst, posed / rest)
    return round(worst, 4)


def loop_error_deg(rig, scene, controls: list[str], first: float, last: float) -> float:
    _set_frame(scene, first)
    a = {c: rig.pose.bones[c].matrix.to_quaternion() for c in controls}
    _set_frame(scene, last)
    worst = 0.0
    for c in controls:
        worst = max(worst, math.degrees(a[c].rotation_difference(rig.pose.bones[c].matrix.to_quaternion()).angle))
    return round(worst, 3)


def control_motion(rig, scene, controls: list[str], frames: list[float]) -> dict[str, float]:
    """Largest rotation (degrees) each control makes away from its first-frame pose."""
    _set_frame(scene, frames[0])
    base = {c: rig.pose.bones[c].matrix.to_quaternion() for c in controls}
    out = dict.fromkeys(controls, 0.0)
    for f in frames[1:]:
        _set_frame(scene, f)
        for c in controls:
            d = math.degrees(base[c].rotation_difference(rig.pose.bones[c].matrix.to_quaternion()).angle)
            out[c] = max(out[c], d)
    return {c: round(v, 2) for c, v in out.items()}
