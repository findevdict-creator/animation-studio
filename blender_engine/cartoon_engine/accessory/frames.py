"""Attachment frames: where a rig-profile attachment point is on one character's body.

An accessory is modelled around the frame of its attachment point on a reference body
(origin at the frame, metres) and put on any other character by mapping that frame onto
the character: ``T(origin) @ S(size / ref_size)``. All characters of a rig profile share
the rest pose (A-pose arms, front is -Y), so frames need no rotation. The frame also says
which way an item is pushed when it has to be moved clear of the body.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from cartoon_engine.character.skeleton import ARM_DROP_DEG, Skeleton
from cartoon_engine.character.validate import Parts


@dataclass(frozen=True)
class Frame:
    point: str
    origin: np.ndarray
    size: float
    #: unit direction a ``solid`` item moves along to clear the body (None for covers)
    push: np.ndarray | None
    #: unit axis of a limb the item wraps (watch), else None
    axis: np.ndarray | None = None

    def as_dict(self) -> dict:
        return {"point": self.point, "origin": [round(float(v), 5) for v in self.origin], "size": round(self.size, 5),
                "push": None if self.push is None else [round(float(v), 4) for v in self.push]}


def _world_center(obj) -> np.ndarray:
    import bpy

    bpy.context.view_layer.update()  # a new object's matrix_world is stale until evaluated
    m = np.array(obj.matrix_world)
    co = np.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
    return (co.min(axis=0) + co.max(axis=0)) / 2


def eye_radius(obj) -> float:
    co = np.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return float((co.max(axis=0) - co.min(axis=0)).max() / 2)


def palm_normal(side: str) -> np.ndarray:
    d = math.radians(ARM_DROP_DEG)
    m = 1.0 if side == "L" else -1.0
    return np.array([-math.sin(d) * m, 0.0, -math.cos(d)])


def frame(point: str, sk: Skeleton, parts: Parts | None = None) -> Frame:
    J, R = sk.joints, sk.radii
    if point == "face_eyes":
        if parts is None:
            raise ValueError("face_eyes needs the character's eyes")
        left, right = _world_center(parts.eyes["L"]), _world_center(parts.eyes["R"])
        return Frame(point, (left + right) / 2, float(np.linalg.norm(left - right)), np.array([0.0, -1.0, 0.0]))
    if point == "head_top":
        return Frame(point, np.array(sk.head_center), float(sk.head_w), None)
    if point.startswith("wrist_"):
        s = point[-1]
        elbow, wrist = np.array(J[f"elbow.{s}"]), np.array(J[f"wrist.{s}"])
        axis = (wrist - elbow) / np.linalg.norm(wrist - elbow)
        return Frame(point, elbow + (wrist - elbow) * 0.86, float(R[f"wrist.{s}"][0]), None, axis)
    if point.startswith("hand_") and point.endswith("_grip"):
        s = point.split("_")[1]
        palm = np.array(J[f"palm.{s}"])
        n = palm_normal(s)
        origin = palm + n * R[f"palm.{s}"][1] * 1.4
        outward = np.array([1.0 if s == "L" else -1.0, 0.0, 0.0])
        return Frame(point, origin, float(np.linalg.norm(np.array(J[f"middle_3.{s}"]) - np.array(J[f"wrist.{s}"]))),
                     outward)
    if point == "back_center":
        chest = np.array(J["chest"])
        return Frame(point, chest + np.array([0.0, R["chest"][1], 0.0]), float(R["chest"][0]),
                     np.array([0.0, 1.0, 0.0]))
    raise KeyError(f"no frame for attachment point {point!r}")
