"""Facial states: what each expression, viseme and eye key does, as numbers. Pure Python.

The head mesh is built from a ``FaceState`` (``head.py``); each shape key is the head built
from the neutral state with one region changed, so expressions, visemes and eye keys are
deltas of the same parametric parts (brows, eyelids, mouth) and their names match the rig
profile exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from studio_contracts.rig_profile import CARTOON_HUMANOID_V1, RigProfile


@dataclass(frozen=True)
class Brow:
    raise_: float = 0.0  # -1..1, whole brow up
    inner: float = 0.0  # -1..1, inner end up (sad, fearful) or down (angry)
    squeeze: float = 0.0  # 0..1, towards the nose


@dataclass(frozen=True)
class Lids:
    upper: float = 0.0  # degrees added to the upper lid edge (negative closes)
    lower: float = 0.0  # degrees added to the lower lid edge (positive closes)


@dataclass(frozen=True)
class Mouth:
    smile: float = 0.0  # -1 (frown) .. 1
    open: float = 0.0  # 0..1
    wide: float = 0.0  # -1 (narrow) .. 1
    round: float = 0.0  # 0..1, O shape
    skew: float = 0.0  # -1..1, one corner up, the other down
    press: float = 0.0  # 0..1, lips pressed together (MBP)


@dataclass(frozen=True)
class FaceState:
    brow_L: Brow = field(default_factory=Brow)
    brow_R: Brow = field(default_factory=Brow)
    lids_L: Lids = field(default_factory=Lids)
    lids_R: Lids = field(default_factory=Lids)
    mouth: Mouth = field(default_factory=Mouth)


NEUTRAL = FaceState()


def _both_brows(**kw) -> dict:
    return {"brow_L": Brow(**kw), "brow_R": Brow(**kw)}


def _both_lids(upper: float = 0.0, lower: float = 0.0) -> dict:
    return {"lids_L": Lids(upper, lower), "lids_R": Lids(upper, lower)}


#: expression -> (brow region, eyes region, mouth region)
EXPRESSIONS: dict[str, tuple[dict, dict, dict]] = {
    "happy": (_both_brows(raise_=0.3, inner=0.1), _both_lids(lower=22), {"mouth": Mouth(smile=0.85, open=0.15)}),
    "sad": (_both_brows(raise_=-0.1, inner=0.9), _both_lids(upper=-14, lower=4), {"mouth": Mouth(smile=-0.7)}),
    "angry": (_both_brows(raise_=-0.5, inner=-1.0, squeeze=0.6), _both_lids(upper=-18, lower=10),
              {"mouth": Mouth(smile=-0.45, wide=0.25, open=0.06)}),
    "surprised": (_both_brows(raise_=1.0), _both_lids(upper=20, lower=-8), {"mouth": Mouth(open=0.8, round=0.7)}),
    "fearful": (_both_brows(raise_=0.6, inner=0.9), _both_lids(upper=16, lower=-6),
                {"mouth": Mouth(open=0.35, wide=0.4, smile=-0.35)}),
    "confused": ({"brow_L": Brow(raise_=0.7), "brow_R": Brow(raise_=-0.35, inner=-0.3)},
                 {"lids_L": Lids(), "lids_R": Lids(upper=-12)}, {"mouth": Mouth(skew=0.6, smile=-0.1)}),
    "excited": (_both_brows(raise_=0.75), _both_lids(upper=14), {"mouth": Mouth(smile=1.0, open=0.6, wide=0.3)}),
    "laughing": (_both_brows(raise_=0.4, inner=0.25), _both_lids(upper=-42, lower=30),
                 {"mouth": Mouth(smile=1.0, open=0.9, wide=0.2)}),
    "crying": (_both_brows(raise_=-0.2, inner=1.0), _both_lids(upper=-24, lower=14),
               {"mouth": Mouth(smile=-0.85, open=0.4, wide=0.3)}),
}

VISEMES: dict[str, Mouth] = {
    "AI": Mouth(open=0.6, wide=0.2),
    "E": Mouth(open=0.3, wide=0.6, smile=0.2),
    "O": Mouth(open=0.55, round=0.85),
    "U": Mouth(open=0.2, round=1.0, wide=-0.4),
    "FV": Mouth(open=0.06, wide=0.1, press=0.4),
    "L": Mouth(open=0.4, wide=0.05),
    "MBP": Mouth(press=1.0, wide=-0.1),
    "WQ": Mouth(open=0.15, round=0.9, wide=-0.6),
    "TH": Mouth(open=0.2, wide=0.1),
    "rest": Mouth(open=0.03),
}

#: blink: the upper lid comes down past the lower lid's raised edge
BLINK_UPPER, BLINK_LOWER = -80.0, 25.0


def eye_key_state(key: str) -> FaceState:
    side = key[-1]
    lids = {"blink": Lids(BLINK_UPPER, BLINK_LOWER), "wide": Lids(20, -10), "squint": Lids(-12, 24)}[key.split("_")[1]]
    return replace(NEUTRAL, **{f"lids_{side}": lids})


def shape_key_states(profile: RigProfile = CARTOON_HUMANOID_V1) -> dict[str, FaceState]:
    """Every shape key the rig profile names -> the face state it represents."""
    states: dict[str, FaceState] = {}
    regions = dict(zip(profile.expression_regions, range(3)))
    for e in profile.expressions:
        parts = EXPRESSIONS[e]
        for region, i in regions.items():
            states[f"expr_{e}_{region}"] = replace(NEUTRAL, **parts[i])
    for v in profile.visemes:
        states[f"vis_{v}"] = replace(NEUTRAL, mouth=VISEMES[v])
    for k in profile.eye_keys:
        states[k] = eye_key_state(k)
    missing = set(profile.shape_keys()) - set(states)
    if missing:
        raise KeyError(f"no face state for {sorted(missing)}")
    return states
