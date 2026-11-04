"""Joint positions and limb radii for a resolved character. Pure Python (no bpy), so the
geometry, the rig fitting and the tests all read the same numbers.

Blender axes: +Z up, the character faces -Y, its left side is +X (Rigify's ``.L``).
Lengths are metres. The figure stands in a relaxed A-pose with arms 50 degrees below
horizontal, a slight elbow and knee bend (so IK has a pole), palms facing down and back.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from studio_contracts.character import ResolvedCharacter

Vec = tuple[float, float, float]
FINGERS = ("thumb", "index", "middle", "ring", "pinky")
ARM_DROP_DEG = 50.0

#: head width / height per face shape, and how much narrower the jaw is than the cranium
FACE_SHAPES = {"rounded": (0.94, 0.92), "oval": (0.82, 0.86), "square": (0.9, 1.0), "heart": (0.9, 0.72)}


def add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def lerp(a: Vec, b: Vec, t: float) -> Vec:
    return add(a, mul(sub(b, a), t))


def length(a: Vec) -> float:
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def norm(a: Vec) -> Vec:
    n = length(a)
    return mul(a, 1.0 / n) if n else a


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def mirror(a: Vec) -> Vec:
    return (-a[0], a[1], a[2])


@dataclass
class Skeleton:
    height: float
    head_h: float
    head_w: float
    head_d: float
    head_center: Vec
    jaw_narrow: float
    joints: dict[str, Vec] = field(default_factory=dict)
    #: skin radius (x, y) at a joint, in metres
    radii: dict[str, tuple[float, float]] = field(default_factory=dict)

    def j(self, name: str) -> Vec:
        return self.joints[name]

    def sided(self, base: str) -> list[str]:
        return [f"{base}.L", f"{base}.R"]


def build_skeleton(rc: ResolvedCharacter) -> Skeleton:
    H = rc.height_m
    head_h = H * rc.head_body_ratio
    fw, jaw = FACE_SHAPES.get(rc.face_shape, FACE_SHAPES["rounded"])
    fs = rc.face_sliders
    head_w = head_h * fw * (1 + 0.06 * fs.get("cheek_fullness", 0.0))
    head_d = head_h * 0.92
    jaw_narrow = jaw * (1 + 0.15 * fs.get("jaw_width", 0.0))
    chin_drop = 0.05 * head_h * fs.get("chin_length", 0.0)
    head_center: Vec = (0.0, 0.005 * H, H - head_h / 2 + chin_drop / 2)

    p = rc.proportions
    Hb = H - head_h  # body below the chin
    neck_len = 0.075 * Hb * (1 + 0.4 * p.get("neck_length", 0.0))
    hip_z = 0.48 * Hb * (1 + 0.12 * p.get("leg_length", 0.0) - 0.10 * p.get("torso_length", 0.0))
    shoulder_z = H - head_h - neck_len + chin_drop
    shoulder_half = 0.16 * Hb * rc.torso_width * (1 + 0.18 * p.get("shoulder_width", 0.0))
    hip_half = 0.088 * Hb * rc.torso_width * (1 + 0.2 * p.get("hip_width", 0.0))
    arm_len = 0.40 * Hb * (1 + 0.12 * p.get("arm_length", 0.0))
    hand_len = 0.15 * Hb * (1 + 0.2 * p.get("hand_size", 0.0))
    foot_len = 0.16 * Hb * (1 + 0.2 * p.get("foot_size", 0.0))
    r = rc.limb_thickness * H * rc.limb
    belly = rc.belly * (1 + 0.3 * p.get("belly", 0.0))

    sk = Skeleton(H, head_h, head_w, head_d, head_center, jaw_narrow)
    J, R = sk.joints, sk.radii

    # spine: pelvis -> chest -> neck -> head (Rigify spine .. spine.006)
    pelvis = (0.0, 0.0, hip_z + 0.04 * Hb)
    neck_base = (0.0, 0.01 * Hb, shoulder_z)
    J["pelvis"] = pelvis
    J["spine1"] = lerp(pelvis, neck_base, 0.28)
    J["spine2"] = lerp(pelvis, neck_base, 0.55)
    J["chest"] = lerp(pelvis, neck_base, 0.80)
    J["neck_base"] = neck_base
    J["neck_mid"] = (0.0, 0.012 * Hb, shoulder_z + 0.5 * neck_len)
    J["head_base"] = (0.0, head_center[1], H - head_h * 0.85)
    J["head_top"] = (0.0, head_center[1], H)
    J["crotch"] = (0.0, 0.0, hip_z - 0.02 * Hb)

    chest_rx = shoulder_half * 0.82
    R["pelvis"] = (hip_half * 1.6, hip_half * 0.95)
    R["crotch"] = (hip_half * 0.8, hip_half * 0.7)
    R["spine1"] = (hip_half * 1.35 * (0.9 + 0.1 * belly), hip_half * 0.95 * belly)
    R["spine2"] = (chest_rx * 0.9 * (0.92 + 0.08 * belly), chest_rx * 0.62 * belly)
    R["chest"] = (chest_rx, chest_rx * 0.62)
    R["neck_base"] = (head_w * 0.2, head_w * 0.18)
    R["neck_mid"] = (head_w * 0.16, head_w * 0.16)
    R["head_base"] = (head_w * 0.15, head_w * 0.15)

    drop = math.radians(ARM_DROP_DEG)
    arm_dir: Vec = (math.cos(drop), 0.0, -math.sin(drop))
    palm_n: Vec = (-math.sin(drop), 0.0, -math.cos(drop))  # palm faces down along the arm
    across: Vec = (0.0, -1.0, 0.0)  # thumb side is the front

    for s, m in (("L", 1.0), ("R", -1.0)):
        def put(name: str, v: Vec, radius: tuple[float, float] | None = None) -> None:
            v = (v[0] * m, v[1], v[2])
            J[f"{name}.{s}"] = v
            if radius:
                R[f"{name}.{s}"] = radius

        clav = (0.025 * Hb, -0.005 * Hb, shoulder_z - 0.01 * Hb)
        shoulder = (shoulder_half, 0.01 * Hb, shoulder_z - 0.035 * Hb)
        upper = arm_len * 0.5
        elbow = add(add(shoulder, mul(arm_dir, upper)), (0.0, 0.012 * Hb, 0.0))
        wrist = add(elbow, mul(arm_dir, arm_len * 0.48))
        put("clavicle", clav)
        put("shoulder", shoulder, (r * 0.85, r * 0.85))
        put("elbow", elbow, (r * 0.68, r * 0.68))
        put("wrist", wrist, (r * 0.55, r * 0.48))

        # hand: palm, then four fingers spread front-to-back and a thumb near the wrist
        palm_end = add(wrist, mul(arm_dir, hand_len * 0.5))
        put("palm", lerp(wrist, palm_end, 0.5), (hand_len * 0.3, hand_len * 0.14))
        finger_r = hand_len * 0.085
        seg = hand_len * 0.5 / 3
        for i, f in enumerate(("index", "middle", "ring", "pinky")):
            spread = (1.5 - i) * hand_len * 0.13  # index at the front
            knuckle = add(palm_end, mul(across, spread))
            fdir = norm(add(arm_dir, mul(across, spread / hand_len * 0.6)))
            flen = seg * (1.0, 1.08, 1.0, 0.82)[i]
            put(f"{f}_0", knuckle, (finger_r, finger_r))
            for k in (1, 2, 3):
                bend = mul(palm_n, 0.12 * flen * k)  # a gentle curl
                put(f"{f}_{k}", add(add(knuckle, mul(fdir, flen * k)), bend), (finger_r * (1 - 0.1 * k),) * 2)
        thumb_base = add(add(wrist, mul(arm_dir, hand_len * 0.12)), add(mul(across, hand_len * 0.2), mul(palm_n, hand_len * 0.08)))
        tdir = norm(add(add(arm_dir, mul(across, 0.9)), mul(palm_n, 0.4)))
        put("thumb_0", thumb_base, (finger_r * 1.15,) * 2)
        for k in (1, 2, 3):
            put(f"thumb_{k}", add(thumb_base, mul(tdir, seg * 0.95 * k)), (finger_r * (1.1 - 0.1 * k),) * 2)

        hip = (hip_half, 0.0, hip_z)
        knee = (hip_half * 0.95, -0.012 * Hb, hip_z * 0.5)
        ankle = (hip_half * 0.9, 0.01 * Hb, 0.055 * Hb)
        thigh = min(r * 1.25, hip_half * 0.88)
        put("hip", hip, (thigh, thigh * 0.96))
        put("knee", knee, (thigh * 0.72, thigh * 0.72))
        put("ankle", ankle, (thigh * 0.55, thigh * 0.5))
        put("heel", (hip_half * 0.9, ankle[1] + foot_len * 0.22, 0.02 * Hb), (r * 0.6, r * 0.5))
        put("ball", (hip_half * 0.92, ankle[1] - foot_len * 0.55, 0.025 * Hb), (r * 0.7, r * 0.42))
        put("toe", (hip_half * 0.92, ankle[1] - foot_len * 0.78, 0.022 * Hb), (r * 0.6, r * 0.36))
    return sk


#: (segment name, region, from joint, to joint); a segment's name is the DEF bone that will
#: own it. Regions are the rig profile's mask regions.
def segments(sk: Skeleton) -> list[tuple[str, str, str, str]]:
    segs = [
        ("DEF-spine", "torso", "pelvis", "spine1"), ("DEF-spine.001", "torso", "spine1", "spine2"),
        ("DEF-spine.002", "torso", "spine2", "chest"), ("DEF-spine.003", "torso", "chest", "neck_base"),
        ("DEF-spine.004", "neck", "neck_base", "neck_mid"), ("DEF-spine.005", "neck", "neck_mid", "head_base"),
        ("DEF-spine.006", "head", "head_base", "head_top"),
    ]
    for s in "LR":
        segs += [
            (f"DEF-shoulder.{s}", "torso", "chest", f"shoulder.{s}"),
            (f"DEF-upper_arm.{s}", "upper_arm", f"shoulder.{s}", f"elbow.{s}"),
            (f"DEF-forearm.{s}", "forearm", f"elbow.{s}", f"wrist.{s}"),
            (f"DEF-hand.{s}", "hand", f"wrist.{s}", f"palm.{s}"),
            (f"DEF-thigh.{s}", "thigh", f"hip.{s}", f"knee.{s}"),
            (f"DEF-shin.{s}", "shin", f"knee.{s}", f"ankle.{s}"),
            (f"DEF-foot.{s}", "foot", f"ankle.{s}", f"ball.{s}"),
            (f"DEF-foot.{s}", "foot", f"ankle.{s}", f"heel.{s}"),
            (f"DEF-toe.{s}", "foot", f"ball.{s}", f"toe.{s}"),
        ]
        for f in FINGERS:
            bone = "thumb" if f == "thumb" else f"f_{f}"
            start = f"wrist.{s}" if f == "thumb" else f"palm.{s}"
            if f != "thumb":
                segs.append((f"DEF-hand.{s}", "hand", start, f"{f}_0.{s}"))
            for k in (1, 2, 3):
                segs.append((f"DEF-{bone}.0{k}.{s}", "hand", f"{f}_{k - 1}.{s}", f"{f}_{k}.{s}"))
    return segs


def segment_distance(p: Vec, a: Vec, b: Vec) -> tuple[float, float]:
    """(distance from p to segment ab, parameter t of the closest point in 0..1)."""
    ab = sub(b, a)
    denom = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2
    t = 0.0 if denom == 0 else max(0.0, min(1.0, sum(x * y for x, y in zip(sub(p, a), ab)) / denom))
    return length(sub(p, lerp(a, b, t))), t


def _radius_at(sk: Skeleton, joint: str) -> float:
    rad = sk.radii.get(joint)
    return max(rad) if rad else 0.02 * sk.height


def nearest_segments(sk: Skeleton, p: Vec, segs=None, k: int = 2) -> list[tuple[float, tuple[str, str, str, str], float]]:
    """The ``k`` segments closest to ``p``, with distance measured in units of the local
    limb radius so a point on a thick thigh is not claimed by a thin hand nearby.
    Returns (normalised distance, segment, t)."""
    out = []
    for seg in segs or segments(sk):
        _, _, a, b = seg
        d, t = segment_distance(p, sk.j(a), sk.j(b))
        r = _radius_at(sk, a) * (1 - t) + _radius_at(sk, b) * t
        out.append((d / max(r, 1e-4), seg, t))
    out.sort(key=lambda x: x[0])
    return out[:k]
