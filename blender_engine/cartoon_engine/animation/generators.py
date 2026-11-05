"""The procedural generators of the animation library (``GEN-*@1.0``).

A generator poses the controls of a rig of profile ``cartoon_humanoid@1.0`` frame by frame;
``bake`` keys each frame into one Action. Poses are computed on the body they are baked on
(its leg length, reach and feet), so the walk's planted foot stays still on that body; on a
body with other proportions the Action's location keys are scaled by leg length when it is
applied (``cartoon_engine.animation.apply``).

Conventions: frames run from 0 to ``frames`` inclusive and a looping generator's last frame
equals its first; the character faces -Y; angles are in degrees.

The walk is a heel-toe walk on IK feet: a foot is planted for 60 % of the cycle and moves
backwards under the body at the travel speed (so it is still in the world when the scene
moves the character forward at that speed), rolls over its heel at heel strike and over the
ball of the foot at toe-off (the pivot is the point that is on the ground), and arcs forward
in the swing. The hips bob, sway over the planted foot and twist with the legs; the chest
counter-twists and the arms swing against the legs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from studio_contracts.animation import ResolvedAnimation
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1, RigProfile

from cartoon_engine.animation import rig as R

LEG_IK = {"thigh_parent.L": 0.0, "thigh_parent.R": 0.0}
ARM_FK = {"upper_arm_parent.L": 1.0, "upper_arm_parent.R": 1.0}
ARMS = tuple(f"{b}.{s}" for s in R.SIDES for b in ("upper_arm_fk", "forearm_fk", "hand_fk"))
FEET = tuple(f"foot_ik.{s}" for s in R.SIDES)
SPINE = ("torso", "hips", "chest", "neck", "head")


def _bump(x: float) -> float:
    """1 at 0, easing to 0 at -1 and 1."""
    return math.cos(x * math.pi / 2) ** 2 if abs(x) < 1 else 0.0


def _smooth(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def _ease_envelope(f: float, n: int, ease_in: int, ease_out: int) -> float:
    return min(_smooth(f / max(ease_in, 1)), _smooth((n - f) / max(ease_out, 1)))


@dataclass
class Generator:
    """One baked motion: what it keys and how it poses a frame."""

    anim: ResolvedAnimation
    geo: R.LegGeometry
    frames: int
    rot: tuple[str, ...] = ()
    loc: tuple[str, ...] = ()
    switches: dict[str, float] = field(default_factory=dict)
    face: tuple[str, ...] = ()
    #: forward travel per frame in the scene for a travelling generator (armature units)
    speed: float = 0.0

    @property
    def p(self) -> dict[str, float]:
        return self.anim.params

    def prepare(self) -> None:
        pass

    def pose(self, rig, f: int) -> dict[str, float]:  # returns face-property values
        raise NotImplementedError


# ------------------------------------------------------------------ helpers
def _feet_at_rest(rig, geo: R.LegGeometry) -> None:
    for s in R.SIDES:
        R.place(rig, f"foot_ik.{s}", rig.pose.bones[f"foot_ik.{s}"].bone.matrix_local.copy())


def _torso_offset(rig, geo: R.LegGeometry, offset, yaw: float = 0.0) -> None:
    from mathutils import Matrix, Vector

    rest = rig.pose.bones["torso"].bone.matrix_local
    m = Matrix.Translation(Vector(offset)) @ R.rotation_about(geo.torso_head, R.Z, yaw) @ rest
    R.place(rig, "torso", m)


def _soft_knees(geo: R.LegGeometry) -> float:
    """How far the hips sit below rest so a standing leg is not locked straight."""
    return 0.012 * geo.leg_length


# ------------------------------------------------------------------ walk
class Walk(Generator):
    DUTY = 0.6  # share of the cycle a foot is planted
    STRIKE = 0.12  # share of the stance spent rolling off the heel
    TOE_OFF = 0.62  # share of the stance after which the heel lifts
    HEEL_DEG = 12.0
    TOE_DEG = 28.0
    REACH = 0.965  # the hip stays within this share of thigh + shin from the ankle

    def prepare(self) -> None:
        g, p, n = self.geo, self.p, self.frames
        L = g.leg_length
        self.step = p["stride"] * L
        self.speed = 2 * self.step / n
        self.range = self.speed * self.DUTY * n  # fore-aft travel of a planted foot under the body
        self.lift = (0.04 + 0.12 * p["lift"]) * L
        self.rot = (*ARMS, "hips", "chest", "head", "torso", *FEET, "toe_ik.L", "toe_ik.R")
        self.loc = ("torso", *FEET)
        self.switches = {**LEG_IK, **ARM_FK}
        self.feet = {s: [self._foot(s, f) for f in range(n + 1)] for s in R.SIDES}
        self.hip_z = self._hip_heights()

    def _phase(self, side: str, f: int) -> float:
        return ((f / self.frames) + (0.0 if side == "L" else 0.5)) % 1.0

    def _foot(self, side: str, f: int):
        """(offset, pivot, pitch): the foot's translation from rest, the ground point it
        rolls about and the roll angle (+ heel up, - toe up)."""
        foot = self.geo.feet[side]
        phi = self._phase(side, f)
        half = self.range / 2
        if phi < self.DUTY:
            u = phi / self.DUTY
            dy, dz = -half + self.range * u, 0.0
            if u < self.STRIKE:
                pitch = -self.HEEL_DEG * (1 - u / self.STRIKE)
            elif u > self.TOE_OFF:
                pitch = self.TOE_DEG * ((u - self.TOE_OFF) / (1 - self.TOE_OFF))
            else:
                pitch = 0.0
        else:
            u = (phi - self.DUTY) / (1 - self.DUTY)
            s = _smooth(u)
            dy, dz = half - self.range * s, self.lift * math.sin(math.pi * u)
            pitch = self.TOE_DEG * (1 - s) - self.HEEL_DEG * s
        pivot = foot.ball_joint if pitch > 0 else foot.heel
        return (0.0, dy, dz), (pivot[0], pivot[1] + dy, pivot[2] + dz), pitch

    def _ankle(self, side: str, f: int) -> np.ndarray:
        from mathutils import Matrix, Vector

        off, pivot, pitch = self.feet[side][f]
        m = R.rotation_about(pivot, R.X, pitch) @ Matrix.Translation(Vector(off))
        return np.array(m @ Vector(self.geo.feet[side].ankle))

    def _hip_heights(self) -> np.ndarray:
        """Hip offset per frame: lowered for soft knees, bobbing (low at heel strike), never
        so high that a leg has to stretch to reach its ankle; smoothed around the loop."""
        g, n, L = self.geo, self.frames, self.geo.leg_length
        bob = (0.004 + 0.022 * self.p["bounce"]) * L
        want = np.array([-_soft_knees(g) - 0.01 * L - bob * math.cos(4 * math.pi * f / n) for f in range(n + 1)])
        limit = np.full(n + 1, np.inf)
        for s in R.SIDES:
            hip_x = g.feet[s].ankle[0]
            for f in range(n + 1):
                a = self._ankle(s, f)
                horiz = (a[0] - hip_x) ** 2 + (a[1] - g.torso_head[1]) ** 2
                dz = math.sqrt(max((self.REACH * g.reach) ** 2 - horiz, 1e-6))
                limit[f] = min(limit[f], a[2] + dz - g.hip_z)
        z = np.minimum(want, limit)[:-1]
        for _ in range(3):  # cyclic smoothing, then clamp again
            z = np.minimum((np.roll(z, 1) + 2 * z + np.roll(z, -1)) / 4, limit[:-1])
        return np.append(z, z[0])

    def pose(self, rig, f: int) -> dict[str, float]:
        from mathutils import Matrix, Vector

        n, p, L = self.frames, self.p, self.geo.leg_length
        phi = f / n
        sway = (0.006 + 0.016 * p["bounce"]) * L * math.sin(2 * math.pi * phi)
        yaw = -5.0 * math.cos(2 * math.pi * phi)
        _torso_offset(rig, self.geo, (sway, 0.0, float(self.hip_z[f])))
        R.turn(rig, "hips", R.Z, yaw)
        R.turn(rig, "chest", R.Z, -1.4 * yaw)
        R.turn(rig, "chest", R.X, 4.0)
        R.turn(rig, "head", R.X, -3.0)
        for s in R.SIDES:
            off, pivot, pitch = self.feet[s][f]
            rest = rig.pose.bones[f"foot_ik.{s}"].bone.matrix_local
            R.place(rig, f"foot_ik.{s}", R.rotation_about(pivot, R.X, pitch) @ Matrix.Translation(Vector(off)) @ rest)
            if pitch > 0:  # the toes bend at the ball and stay flat while the heel lifts
                R.turn(rig, f"toe_ik.{s}", R.X, -pitch)
        R.arms_down(rig)
        amp = 8.0 + 34.0 * p["arm_swing"]
        for s, sign in (("L", -1.0), ("R", 1.0)):
            a = sign * amp * math.cos(2 * math.pi * phi)  # + is forward; each arm against its leg
            R.swing(rig, f"upper_arm_fk.{s}", R.FWD if a > 0 else R.BACK, abs(a))
            R.swing(rig, f"forearm_fk.{s}", R.FWD, 12.0 + 18.0 * max(a, 0.0) / amp)
        return {}


# ------------------------------------------------------------------ idle and talk
class Idle(Generator):
    def prepare(self) -> None:
        self.rot = (*ARMS, "chest", "head", "torso", *FEET)
        self.loc = ("torso", *FEET)
        self.switches = {**LEG_IK, **ARM_FK}

    def _base(self, rig, f: int, sway_amp: float, elbows: float) -> float:
        n, L = self.frames, self.geo.leg_length
        ph = 2 * math.pi * f / n
        _torso_offset(rig, self.geo, (sway_amp * L * math.sin(ph), 0.0, -_soft_knees(self.geo)))
        _feet_at_rest(rig, self.geo)
        R.arms_down(rig)
        for s in R.SIDES:
            R.swing(rig, f"forearm_fk.{s}", R.FWD, elbows)
        return ph

    def pose(self, rig, f: int) -> dict[str, float]:
        p = self.p
        ph = self._base(rig, f, 0.012 * p["sway"], 8.0)
        R.turn(rig, "chest", R.X, -1.6 * p["breath"] * math.sin(2 * ph))
        R.turn(rig, "head", R.X, 1.0 * p["breath"] * math.sin(2 * ph))
        R.turn(rig, "head", R.Z, 3.0 * p["sway"] * math.sin(ph + 0.8))
        return {}


class Talk(Idle):
    def prepare(self) -> None:
        super().prepare()
        n, b = self.frames, max(1, int(round(self.p["beats"])))
        self.beats = [((k + 0.5) / b * n, "R" if k % 2 == 0 else "L") for k in range(b)]
        self.width = n / (1.6 * b)

    def _beat(self, f: float, side: str | None = None) -> float:
        n, e = self.frames, 0.0
        for c, s in self.beats:
            if side is None or s == side:
                d = (f - c + n / 2) % n - n / 2  # periodic distance, so the clip loops
                e = max(e, _bump(d / self.width))
        return e

    def pose(self, rig, f: int) -> dict[str, float]:
        p = self.p
        ph = self._base(rig, f, 0.006, 20.0)
        k = p["intensity"]
        for s, weight in (("R", 1.0), ("L", 0.6)):
            e = self._beat(f, s) * weight
            R.swing(rig, f"upper_arm_fk.{s}", R.FWD, 6.0 + 18.0 * k * e)
            R.swing(rig, f"forearm_fk.{s}", R.UP, 10.0 + 40.0 * k * e)
            R.swing(rig, f"hand_fk.{s}", R.UP, 12.0 * k * e)
        e = self._beat(f)
        R.turn(rig, "chest", R.Z, 3.0 * k * math.sin(ph))
        R.turn(rig, "chest", R.X, -1.2 * math.sin(2 * ph))
        R.turn(rig, "head", R.X, 7.0 * p["head"] * e)
        R.turn(rig, "head", R.Y, 4.0 * p["head"] * math.sin(ph + 1.3))
        return {}


# ------------------------------------------------------------------ gestures
class Wave(Generator):
    def prepare(self) -> None:
        self.rot = (*ARMS,)
        self.switches = dict(ARM_FK)

    def pose(self, rig, f: int) -> dict[str, float]:
        p, n = self.p, self.frames
        R.arms_down(rig, ("L",))
        up = 0.35 + 0.5 * p["height"]
        R.aim(rig, "upper_arm_fk.R", (-0.75, -0.2, up))  # up and out to the side (R is -X)
        side = math.sin(2 * math.pi * p["waves"] * f / n)
        R.aim(rig, "forearm_fk.R", (-0.45 * side, -0.15, 1.0))
        R.swing(rig, "hand_fk.R", (-1.0, 0.0, 0.0) if side > 0 else (1.0, 0.0, 0.0), 10.0 * abs(side))
        return {}


class Nod(Generator):
    def prepare(self) -> None:
        self.rot = ("head", "neck")

    def pose(self, rig, f: int) -> dict[str, float]:
        n, d = self.frames, 6.0 + 18.0 * self.p["depth"]
        e = _bump((f - n * 0.27) / (n * 0.22)) + 0.7 * _bump((f - n * 0.7) / (n * 0.2))
        R.turn(rig, "neck", R.X, 0.3 * d * e)
        R.turn(rig, "head", R.X, 0.7 * d * e)
        return {}


class Point(Generator):
    def prepare(self) -> None:
        self.rot = tuple(b for b in ARMS if b.endswith(".L"))
        self.switches = {"upper_arm_parent.L": 1.0}

    def pose(self, rig, f: int) -> dict[str, float]:
        n = self.frames
        e = _ease_envelope(f, n, int(n * 0.3), 1) if f < n else 1.0
        R.arms_down(rig, ("L",))
        R.swing(rig, "upper_arm_fk.L", R.FWD, e * (60.0 + 30.0 * self.p["height"]))
        R.swing(rig, "hand_fk.L", R.UP, 8.0 * e)
        return {}


class Look(Generator):
    def prepare(self) -> None:
        self.rot = ("head", "neck")

    def pose(self, rig, f: int) -> dict[str, float]:
        a = (15.0 + 30.0 * self.p["range"]) * math.sin(2 * math.pi * f / self.frames)
        R.turn(rig, "neck", R.Z, 0.35 * a)
        R.turn(rig, "head", R.Z, 0.65 * a)
        R.turn(rig, "head", R.X, -3.0 * abs(math.sin(2 * math.pi * f / self.frames)))
        return {}


# ------------------------------------------------------------------ expression takes
# head and chest acting per expression: (head down, head turn, head tilt, chest forward,
# shake frequency in cycles per take, shake degrees)
ACTING: dict[str, tuple[float, float, float, float, float, float]] = {
    "happy": (-3.0, 0.0, 6.0, -3.0, 0.0, 0.0),
    "sad": (14.0, 0.0, 3.0, 6.0, 0.0, 0.0),
    "angry": (6.0, 0.0, 0.0, 5.0, 0.0, 0.0),
    "surprised": (-11.0, 0.0, 0.0, -5.0, 0.0, 0.0),
    "fearful": (-6.0, 12.0, 0.0, -4.0, 6.0, 1.5),
    "confused": (2.0, 0.0, 12.0, 0.0, 0.0, 0.0),
    "excited": (-5.0, 0.0, 0.0, -3.0, 4.0, 3.0),
    "laughing": (-8.0, 0.0, 3.0, -3.0, 6.0, 4.0),
    "crying": (16.0, 0.0, 0.0, 8.0, 5.0, 2.0),
}


class Expression(Generator):
    def prepare(self) -> None:
        self.rot = ("head", "neck", "chest")
        self.face = (f"expr_{self.anim.expression}",)

    def pose(self, rig, f: int) -> dict[str, float]:
        n, p = self.frames, self.p
        e = _ease_envelope(f, n, 8, 12)
        down, turn_, tilt, chest, freq, shake = ACTING[self.anim.expression or "happy"]
        k = p["body"] * e
        wobble = shake * math.sin(2 * math.pi * freq * f / n) if freq else 0.0
        R.turn(rig, "chest", R.X, k * (chest + 0.5 * wobble))
        R.turn(rig, "neck", R.X, 0.3 * k * down)
        R.turn(rig, "head", R.X, k * (0.7 * down + wobble))
        R.turn(rig, "head", R.Z, k * turn_)
        R.turn(rig, "head", R.Y, k * tilt)
        return {self.face[0]: p["intensity"] * e}


GENERATOR_CLASSES: dict[str, type[Generator]] = {"walk": Walk, "idle": Idle, "talk": Talk, "wave": Wave, "nod": Nod,
                                                 "point": Point, "look_around": Look, "expression": Expression}


def make(anim: ResolvedAnimation, rig) -> Generator:
    gen = GENERATOR_CLASSES[anim.key](anim=anim, geo=R.leg_geometry(rig), frames=int(anim.frames or 24))
    gen.prepare()
    return gen


# ------------------------------------------------------------------ bake
def bake(rig, gen: Generator, name: str, profile: RigProfile = CARTOON_HUMANOID_V1):
    """Key every frame of ``gen`` on ``rig`` into a new Action and return it. The rig is left
    at rest with its own action restored."""
    import bpy

    rig.animation_data_create()
    saved = rig.animation_data.action
    action = bpy.data.actions.new(name)
    rig.animation_data.action = action
    face = rig.pose.bones[profile.face_control_bone]
    touched = sorted({*gen.rot, *gen.loc})
    try:
        for f in range(gen.frames + 1):
            R.reset(rig, touched)
            for prop in gen.face:
                face[prop] = 0.0
            for sw, v in gen.switches.items():
                R.set_switch(rig, sw, v)
            rig.update_tag()
            R.update()
            values = gen.pose(rig, f)
            for b in gen.rot:
                rig.pose.bones[b].keyframe_insert("rotation_quaternion", frame=f, group=b)
            for b in gen.loc:
                rig.pose.bones[b].keyframe_insert("location", frame=f, group=b)
            for sw in gen.switches:
                rig.pose.bones[sw].keyframe_insert('["IK_FK"]', frame=f, group=sw)
            for prop in gen.face:
                face[prop] = float(values.get(prop, 0.0))
                face.keyframe_insert(f'["{prop}"]', frame=f, group="face")
    finally:
        R.reset(rig, touched)
        for prop in gen.face:
            face[prop] = 0.0
        rig.animation_data.action = saved
        R.update()
    for fc in fcurves(action):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"  # every frame is keyed: no overshoot between keys
    action.use_cyclic = gen.anim.loops
    action["studio_generator"] = gen.anim.generator or ""
    action.frame_range = (0.0, float(gen.frames))
    action.use_frame_range = True
    return action


def fcurves(action):
    """Every F-curve of a (Blender 5 layered) Action."""
    out = []
    for layer in action.layers:
        for strip in layer.strips:
            for slot in action.slots:
                bag = strip.channelbag(slot)
                if bag is not None:
                    out.extend(bag.fcurves)
    return out
