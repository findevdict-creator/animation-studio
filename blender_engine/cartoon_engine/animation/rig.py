"""What the animation code needs to know about a rig of profile ``cartoon_humanoid@1.0``:
its leg geometry at rest, and posing controls in armature space.

Every function here works on any character of the profile. The generators run on the rig
at its rest placement (object matrix identity, root at rest), so armature space is world
space; the character faces -Y with +Z up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

FWD = (0.0, -1.0, 0.0)
BACK = (0.0, 1.0, 0.0)
UP = (0.0, 0.0, 1.0)
DOWN = (0.0, 0.0, -1.0)
X = (1.0, 0.0, 0.0)
Y = (0.0, 1.0, 0.0)
Z = (0.0, 0.0, 1.0)
SIDES = ("L", "R")


@dataclass(frozen=True)
class Foot:
    side: str
    ankle: tuple[float, float, float]  # foot_ik head at rest
    ball: tuple[float, float, float]  # toe joint at rest, on the ground
    ball_joint: tuple[float, float, float]  # the toe joint itself (the toe-off pivot)
    tip: tuple[float, float, float]
    heel: tuple[float, float, float]  # back of the heel on the ground (the heel-strike pivot)


@dataclass(frozen=True)
class LegGeometry:
    #: hip height (the thigh's head above the ground): the scale every location key uses
    leg_length: float
    #: thigh plus shin: how far the hip can be from the ankle
    reach: float
    hip_z: float
    torso_head: tuple[float, float, float]
    feet: dict[str, Foot]


def leg_geometry(rig) -> LegGeometry:
    bones = rig.data.bones
    feet = {}
    for s in SIDES:
        ankle = tuple(bones[f"foot_ik.{s}"].head_local)
        ball = tuple(bones[f"toe_ik.{s}"].head_local)
        tip = tuple(bones[f"toe_ik.{s}"].tail_local)
        foot_len = abs(tip[1] - ankle[1])
        heel = (ankle[0], ankle[1] + 0.3 * foot_len, 0.0)
        feet[s] = Foot(side=s, ankle=ankle, ball=(ball[0], ball[1], 0.0), ball_joint=ball, tip=tip, heel=heel)
    thigh = bones["thigh_fk.L"]
    return LegGeometry(leg_length=float(thigh.head_local.z),
                       reach=float(bones["thigh_fk.L"].length + bones["shin_fk.L"].length),
                       hip_z=float(thigh.head_local.z), torso_head=tuple(bones["torso"].head_local), feet=feet)


def update() -> None:
    import bpy

    bpy.context.view_layer.update()


def reset(rig, names) -> None:
    for n in names:
        pb = rig.pose.bones[n]
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)


def rotation_about(point, axis, degrees: float):
    from mathutils import Matrix, Vector

    p = Vector(point)
    return Matrix.Translation(p) @ Matrix.Rotation(math.radians(degrees), 4, Vector(axis)) @ Matrix.Translation(-p)


def place(rig, name: str, matrix) -> None:
    """Set a control's armature-space matrix (its parents must be evaluated)."""
    update()
    rig.pose.bones[name].matrix = matrix
    update()


def swing(rig, name: str, toward, degrees: float) -> None:
    """Rotate a control about its head so its direction turns ``degrees`` toward ``toward``."""
    from mathutils import Vector

    update()
    pb = rig.pose.bones[name]
    v = (pb.tail - pb.head).normalized()
    axis = v.cross(Vector(toward))
    if axis.length < 1e-6 or abs(degrees) < 1e-9:
        return
    pb.matrix = rotation_about(pb.head, axis.normalized(), degrees) @ pb.matrix
    update()


def aim(rig, name: str, direction) -> None:
    """Rotate a control about its head so it points along ``direction`` (armature space)."""
    from mathutils import Vector

    update()
    pb = rig.pose.bones[name]
    v = (pb.tail - pb.head).normalized()
    d = Vector(direction).normalized()
    axis = v.cross(d)
    if axis.length < 1e-6:
        return
    swing(rig, name, d, math.degrees(v.angle(d)))


def turn(rig, name: str, axis, degrees: float) -> None:
    """Rotate a control about its head around an armature-space axis."""
    if abs(degrees) < 1e-9:
        return
    update()
    pb = rig.pose.bones[name]
    pb.matrix = rotation_about(pb.head, axis, degrees) @ pb.matrix
    update()


def arm_down_angle(rig, side: str) -> float:
    """Degrees from the arm's rest direction (the rig's A pose) to hanging 12 degrees out
    from vertical, so clips start from arms at the sides on any proportions."""
    b = rig.data.bones[f"upper_arm_fk.{side}"]
    d = (b.tail_local - b.head_local).normalized()
    from_vertical = math.degrees(math.acos(max(-1.0, min(1.0, -d.z))))
    return max(0.0, from_vertical - 12.0)


def arms_down(rig, sides=SIDES) -> None:
    for s in sides:
        swing(rig, f"upper_arm_fk.{s}", DOWN, arm_down_angle(rig, s))


def set_switch(rig, name: str, value: float) -> None:
    pb = rig.pose.bones[name]
    if "IK_FK" in pb:
        pb["IK_FK"] = float(value)
