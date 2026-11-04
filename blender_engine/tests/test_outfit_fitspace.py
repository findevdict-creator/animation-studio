"""Skeleton space (pure numpy): the mapping that moves garments between characters."""

import numpy as np
import pytest
from cartoon_engine.character.skeleton import build_skeleton
from cartoon_engine.outfit import fitspace as fs
from studio_contracts.character import CharacterSpecification, resolve_spec
from studio_contracts.outfit import reference_character


def skeleton(**body):
    return build_skeleton(resolve_spec(CharacterSpecification(name="x", body={"age_appearance": "child", **body})))


REF = build_skeleton(resolve_spec(reference_character("child_average")))
BIG = skeleton(height_m=1.42, proportions={"belly": 0.8, "shoulder_width": 0.6, "arm_length": -0.4})


def test_identity_and_joints():
    rng = np.random.default_rng(1)
    pts = np.array(list(REF.joints.values())) + rng.normal(0, 0.03, (len(REF.joints), 3))
    assert np.abs(fs.retarget(REF, REF, pts) - pts).max() < 1e-9
    joints = np.array([REF.j(k) for k in REF.joints])
    mapped = fs.retarget(REF, BIG, joints)
    assert np.abs(mapped - np.array([BIG.j(k) for k in REF.joints])).max() < 1e-3  # head joints use the head map


def test_offsets_scale_with_limb_radius():
    a, b = np.array(REF.j("shoulder.L")), np.array(REF.j("elbow.L"))
    mid = (a + b) / 2
    r0 = max(REF.radii["shoulder.L"]) * 0.5 + max(REF.radii["elbow.L"]) * 0.5
    side = np.array([0.0, -1.0, 0.0])  # perpendicular to the arm, in front
    p = mid + side * r0 * 1.2
    q = fs.retarget(REF, BIG, p[None])[0]
    a1, b1 = np.array(BIG.j("shoulder.L")), np.array(BIG.j("elbow.L"))
    r1 = max(BIG.radii["shoulder.L"]) * 0.5 + max(BIG.radii["elbow.L"]) * 0.5
    t = np.dot(q - a1, b1 - a1) / np.dot(b1 - a1, b1 - a1)
    dist = np.linalg.norm(q - (a1 + t * (b1 - a1)))
    assert t == pytest.approx(0.5, abs=0.05)
    assert dist == pytest.approx(r1 * 1.2, rel=0.08)


def test_regions_and_classification():
    keys, _, t = fs.classify(REF, np.array([REF.j("pelvis"), REF.j("knee.L"), REF.j("wrist.R"), REF.j("head_top")]))
    assert keys[0] == "torso" and keys[1] in ("thigh.L", "shin.L") and keys[2] in ("forearm.R", "hand.R")
    assert keys[3] == "head" and (0 <= t).all() and (t <= 1).all()


def test_rigid_map_keeps_shape():
    rng = np.random.default_rng(2)
    c = np.array(REF.head_center)
    cap = c + rng.normal(0, 0.05, (20, 3))
    out = fs.rigid_map(REF, BIG, cap, c, use_head=True)
    d0 = np.linalg.norm(cap[:, None] - cap[None], axis=2)
    d1 = np.linalg.norm(out[:, None] - out[None], axis=2)
    ratio = d1[d0 > 0] / d0[d0 > 0]
    assert ratio.std() < 1e-9  # a uniform scale, no shear
    back = np.array([0.0, 0.08, REF.j("chest")[2]])
    bag = back + rng.normal(0, 0.04, (20, 3))
    out = fs.rigid_map(REF, BIG, bag, back, use_head=False)
    d0 = np.linalg.norm(bag[:, None] - bag[None], axis=2)
    d1 = np.linalg.norm(out[:, None] - out[None], axis=2)
    assert (d1[d0 > 0] / d0[d0 > 0]).std() < 1e-9


def test_skeleton_json_round_trip():
    sk = fs.skeleton_from_json(fs.skeleton_json(BIG))
    assert sk.joints == BIG.joints and sk.radii == BIG.radii and sk.head_center == BIG.head_center
