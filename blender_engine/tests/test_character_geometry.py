"""Stand-in template geometry: every age and hair style builds, the figure has the requested
height, the face has every shape key the rig profile names (and only movable parts move),
and the same specification gives the same mesh."""

import hashlib

import pytest

bpy = pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")

from cartoon_engine.character.build import build_geometry  # noqa: E402
from cartoon_engine.character.face import shape_key_states  # noqa: E402
from cartoon_engine.character.skeleton import build_skeleton, nearest_segments  # noqa: E402
from cartoon_engine.core import blend_io  # noqa: E402
from studio_contracts.character import CharacterSpecification, resolve_spec  # noqa: E402
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1  # noqa: E402


def resolved(**over):
    spec = {"name": "Test", "body": {"age_appearance": "child"}, **over}
    return resolve_spec(CharacterSpecification.model_validate(spec))


def world_bounds(objs):
    from mathutils import Vector

    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]


def mesh_digest(obj) -> str:
    h = hashlib.sha256()
    for v in obj.data.vertices:
        h.update(("%.5f %.5f %.5f" % tuple(v.co)).encode())
    return h.hexdigest()


@pytest.mark.parametrize("age,hair", [("child", "short"), ("teen", "bob"), ("adult", "spiky"), ("elder", "long"),
                                      ("adult", "bald")])
def test_builds_at_the_requested_height(age, hair):
    blend_io.reset_empty()
    rc = resolved(body={"age_appearance": age}, hair={"style": hair, "color": "brown"})
    ch = build_geometry(rc)
    lo, hi = world_bounds([ch.body, ch.head, *ch.eyes.values()])
    assert lo[2] == pytest.approx(0.0, abs=0.02 * rc.height_m)  # feet on the ground
    assert hi[2] - lo[2] == pytest.approx(rc.height_m, rel=0.02)  # height is to the top of the skull
    assert abs(hi[0] + lo[0]) < 0.01  # left-right symmetric
    if ch.hair is not None:
        assert world_bounds([ch.hair])[1][2] < rc.height_m * 1.12
    assert (ch.hair is None) == (hair == "bald")
    assert {m.name for m in ch.body.data.materials} == {"skin", "base_suit"}


def test_shape_keys_match_the_rig_profile_and_move_only_face_parts():
    blend_io.reset_empty()
    ch = build_geometry(resolved())
    blocks = ch.head.data.shape_keys.key_blocks
    assert [b.name for b in blocks][1:] == CARTOON_HUMANOID_V1.shape_keys()
    static = ch.head_parts.static_count
    for b in blocks[1:]:
        assert b.value == 0.0 and b.relative_key.name == "Basis"
        moved = [i for i, (d, base) in enumerate(zip(b.data, blocks[0].data)) if (d.co - base.co).length > 1e-6]
        assert moved, f"{b.name} moves nothing"
        assert min(moved) >= static, f"{b.name} moves the skull"


def test_regions_stay_in_their_region():
    states = shape_key_states()
    for name, st in states.items():
        if name.endswith("_mouth") or name.startswith("vis_"):
            assert st.brow_L == st.brow_R == type(st.brow_L)() and st.lids_L == type(st.lids_L)()
        if name.endswith("_brow"):
            assert st.mouth == type(st.mouth)() and st.lids_L == type(st.lids_L)()


def test_same_spec_same_mesh_and_sliders_change_it():
    blend_io.reset_empty()
    a = build_geometry(resolved())
    da, ha = mesh_digest(a.body), mesh_digest(a.head)
    blend_io.reset_empty()
    b = build_geometry(resolved())
    assert (mesh_digest(b.body), mesh_digest(b.head)) == (da, ha)
    blend_io.reset_empty()
    c = build_geometry(resolved(body={"age_appearance": "child", "proportions": {"leg_length": 0.8}},
                                face={"sliders": {"cheek_fullness": 0.8}}))
    assert mesh_digest(c.body) != da and mesh_digest(c.head) != ha


def test_segments_own_the_right_points():
    sk = build_skeleton(resolved())
    knee = sk.j("knee.L")
    (_, (bone, region, _, _), _), = nearest_segments(sk, (knee[0] + 0.02, knee[1], knee[2] + 0.05), k=1)
    assert bone == "DEF-thigh.L" and region == "thigh"
    wrist = sk.j("wrist.R")
    (_, (bone, _, _, _), _), = nearest_segments(sk, wrist, k=1)
    assert bone in ("DEF-forearm.R", "DEF-hand.R", "DEF-thumb.01.R")
