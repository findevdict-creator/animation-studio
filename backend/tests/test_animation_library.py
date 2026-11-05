"""Animation Library: generators and bone maps, validation, uploads of BVH takes, identity and
category, drafts, the play-check gate on submit, review, publish numbering, the database
guards, and rig compatibility when a scene pins an animation (SRS 30 Phase 6).

Builds are recorded with the same service call the worker makes after a real Blender job
(``workers/tests/test_animation_e2e.py`` runs those jobs for real)."""

import hashlib
import uuid

import pytest
from app.assets import animations as svc
from app.models.animations import AnimationVersion
from sqlalchemy import text

from tests.test_scene_library import cast, manifest  # noqa: F401
from tests.test_scene_library import create as create_scene

ROLES = {"blend": "animation.blend", "spec": "animation.json", "report": "check_report.json",
         "thumbnail": "thumbnail.png", "preview": "frames.png"}
BVH = b"HIERARCHY\nROOT Hips\n{\n OFFSET 0 0 0\n CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation\n" \
      b" End Site\n {\n  OFFSET 0 1 0\n }\n}\nMOTION\nFrames: 1\nFrame Time: 0.0083333\n0 0 0 0 0 0\n"


def gen_spec(key="walk", **kw):
    ids = {"walk": "GEN-WALK", "wave": "GEN-WAVE", "talk": "GEN-TALK", "expression": "GEN-EXPR", "idle": "GEN-IDLE"}
    return {"source": {"kind": "generator", "generator": {"id": ids[key], "version": "1.0"}, **kw.pop("source", {})},
            **kw}


def compat(**kw):
    return {"rig_profile": "cartoon_humanoid@1.0", "category": "locomotion", "frames": 24, "loops": True,
            "travels": True, "controls": ["torso", "foot_ik.L", "foot_ik.R", "upper_arm_fk.L", "thigh_parent.L"],
            "face_props": [], "fit_profiles": [], "reference_fit_profile": "child_average",
            "reference_leg_length_m": 0.408, "stride_m": 0.57, **kw}


def create(api, spec=None, name=None):
    r = api.post("/api/v1/animations", json={"name": name or f"Walk {uuid.uuid4().hex[:6]}", "tags": ["walk"],
                                              "spec": spec or gen_spec()})
    assert r.status_code == 201, r.text
    return r.json()


def fake_build(db, version_id, job_id, *, ok=True, blend_sha="d" * 64, c=None):
    uploaded = [{"role": role, "storage_key": f"jobs/{job_id}/attempt-1/out/{name}",
                 "sha256": blend_sha if role == "blend" else "e" * 64, "size_bytes": 10,
                 "mime_type": "application/octet-stream"} for role, name in ROLES.items()]
    with db.begin() as s:
        v = s.get(AnimationVersion, uuid.UUID(version_id))
        report = {"ok": ok, "failed": [] if ok else ["foot_slide:adult_average"], "animation_blend_sha256": blend_sha,
                  "checks": []}
        return svc.attach_build(s, v, uuid.UUID(job_id), uploaded,
                                {"resolved_digest": v.spec_digest, "report": report, "check": {"ok": ok},
                                 "compat": c or compat()}, "5.2.3")


def generate(api, aid, vid):
    r = api.post(f"/api/v1/animations/{aid}/drafts/{vid}/generate", json={"previews": {"tile": 64}})
    assert r.status_code == 202, r.text
    return r.json()


def built(api, db, spec=None, ok=True, c=None):
    a = create(api, spec)
    vid = a["versions"][0]["id"]
    job = generate(api, a["id"], vid)["job"]
    assert fake_build(db, vid, job["id"], ok=ok, c=c)["attached"]
    return a, vid, job["id"]


def publish_now(db, aid, vid, job_id, major=1, minor=0):
    with db.begin() as s:
        v = svc.get_version(s, uuid.UUID(aid), uuid.UUID(vid), lock=True)
        prefix = svc.library_prefix(v, major, minor)
        files = [{"role": f.role, "storage_key": prefix + f.storage_key.rsplit("/", 1)[-1], "sha256": f.sha256,
                  "size_bytes": f.size_bytes, "mime_type": f.mime_type} for f in v.entry.files]
        svc.complete_publish(s, v, major=major, minor=minor, files=files,
                             manifest={"storage_key": prefix + "manifest.json", "sha256": "f" * 64},
                             published_by=None, expected_build=v.built_digest, job_id=uuid.UUID(job_id))


def published(api_as, db, spec=None, c=None):
    creator = api_as("creator")
    a, vid, job_id = built(creator, db, spec, c=c)
    assert creator.post(f"/api/v1/animations/{a['id']}/drafts/{vid}/submit").status_code == 200
    assert api_as("reviewer").post(f"/api/v1/animations/{a['id']}/versions/{vid}/review",
                                   json={"decision": "approve"}).status_code == 200
    publish_now(db, a["id"], vid, job_id)
    return a, vid


def test_generators_bone_maps_and_validation(api_as):
    api = api_as("reviewer")
    gens = {g["key"]: g for g in api.get("/api/v1/animation-generators").json()}
    assert set(gens) == {"walk", "idle", "talk", "wave", "nod", "point", "look_around", "expression"}
    assert gens["walk"]["travels"] and gens["walk"]["category"] == "locomotion"
    maps = {m["id"]: m for m in api.get("/api/v1/bone-maps").json()}
    assert maps["cmu"]["bones"]["LeftUpLeg"] == "thigh_fk.L"
    ok = api.post("/api/v1/animations/spec/validate", json={"name": "W", **gen_spec(source={"params": {"cadence": 100}})})
    assert ok.json()["valid"] and ok.json()["resolved"]["frames"] == 28
    bad = api.post("/api/v1/animations/spec/validate", json={
        "name": "E", **gen_spec("expression", source={"expression": "smug", "params": {"intensity": 3, "x": 1}},
                                fit_profiles=["giant"])}).json()
    assert not bad["valid"]
    assert {i["code"] for i in bad["issues"]} >= {"unknown_expression", "out_of_range", "unknown_param",
                                                   "unknown_fit_profile"}


def test_bvh_upload_and_source_check(api_as, db):
    creator = api_as("creator")
    r = creator.post("/api/v1/uploads", params={"kind": "bvh", "name": "take.bvh"}, content=BVH,
                     headers={"content-type": "text/plain"})
    assert r.status_code == 201, r.text
    up = r.json()
    assert up["sha256"] == hashlib.sha256(BVH).hexdigest() and up["storage_key"].startswith("uploads/")
    assert creator.post("/api/v1/uploads", params={"kind": "bvh", "name": "x.bvh"}, content=b"nope").status_code == 422
    assert creator.post("/api/v1/uploads", params={"kind": "bvh", "name": "../x.bvh"}, content=BVH).status_code == 422
    spec = {"category": "locomotion", "source": {"kind": "bvh", "upload_key": up["storage_key"], "sha256": up["sha256"],
                                                 "skeleton": "cmu", "licence": "test", "travels": True}}
    a = create(creator, spec)
    v = a["versions"][0]
    assert v["source_kind"] == "bvh"
    generate(creator, a["id"], v["id"])
    # a spec naming bytes nobody uploaded cannot be built
    wrong = create(creator, {**spec, "source": {**spec["source"], "sha256": "0" * 64}})
    r = creator.post(f"/api/v1/animations/{wrong['id']}/drafts/{wrong['versions'][0]['id']}/generate", json={})
    assert r.status_code == 422 and r.json()["code"] == "source_not_uploaded"
    with db() as s, pytest.raises(Exception, match="uploads never change"):
        s.execute(text("UPDATE uploads SET sha256 = 'x' WHERE id = :i"), {"i": up["id"]})
    with db() as s, pytest.raises(Exception, match="source of an animation version"):
        s.execute(text("DELETE FROM uploads WHERE id = :i"), {"i": up["id"]})


def test_failed_check_blocks_submit_then_review_and_category(api_as, db):
    creator, reviewer = api_as("creator"), api_as("reviewer")
    a, vid, _ = built(creator, db, ok=False)
    assert a["code"].startswith("ANIM-") and a["category"] == "locomotion"
    base = f"/api/v1/animations/{a['id']}"
    r = creator.post(f"{base}/drafts/{vid}/submit")
    assert r.status_code == 409 and "play check on the reference bodies failed" in r.json()["detail"]
    r = creator.patch(f"{base}/drafts/{vid}", json={"spec": gen_spec("wave")})
    assert r.status_code == 422 and r.json()["code"] == "category_changed"
    job = generate(creator, a["id"], vid)["job"]
    fake_build(db, vid, job["id"], ok=True)
    detail = creator.post(f"{base}/drafts/{vid}/submit").json()
    assert detail["status"] == "review" and detail["compat"]["stride_m"] == 0.57
    with db() as s, pytest.raises(Exception, match="in review, its build cannot change"):
        s.execute(text("UPDATE animation_versions SET compat = '{}' WHERE id = :i"), {"i": vid})
    r = reviewer.post(f"{base}/versions/{vid}/review", json={"decision": "approve"})
    assert r.json()["review_decision"] == "approve"


def test_published_versions_are_frozen_and_numbering(api_as, db):
    a, vid = published(api_as, db)
    base = f"/api/v1/animations/{a['id']}"
    creator = api_as("creator")
    assert creator.get(base).json()["latest_published"] == "1.0"
    with db() as s, pytest.raises(Exception, match="cannot be changed"):
        s.execute(text("UPDATE animation_versions SET spec = '{}' WHERE id = :i"), {"i": vid})
    with db() as s, pytest.raises(Exception, match="cannot be deleted"):
        s.execute(text("DELETE FROM animation_versions WHERE id = :i"), {"i": vid})
    with db.begin() as s:
        anim = svc.get_animation(s, uuid.UUID(a["id"]))
        same = svc.new_draft(s, anim, based_on_version_id=uuid.UUID(vid), spec=gen_spec(source={"params": {"bounce": 0.8}}),
                             created_by=None)
        same.compat = compat()
        assert svc.next_number(s, same, "minor") == (1, 1, [])
        more = svc.new_draft(s, anim, based_on_version_id=None, spec=gen_spec(), created_by=None)
        more.compat = compat(controls=[*compat()["controls"], "head"])
        assert svc.next_number(s, more, "minor") == (2, 0, ["controls"])


def test_scene_pins_an_animation_and_checks_compatibility(api_as, db, cast):  # noqa: F811
    creator = api_as("creator")
    walk, wvid = published(api_as, db)
    adults = published(api_as, db, gen_spec("wave", fit_profiles=["adult_average"]),
                       c=compat(category="gesture", travels=False, stride_m=0.0, fit_profiles=["adult_average"]))
    other_rig = published(api_as, db, gen_spec("wave"), c=compat(category="gesture", rig_profile="quadruped@1.0"))
    codes = {k: creator.get(f"/api/v1/animations/{x[0]['id']}").json()["code"]
             for k, x in (("walk", (walk, wvid)), ("adults", adults), ("other", other_rig))}
    m = manifest(cast["codes"])
    m["instances"][0]["animations"] = [{"clip": f"{codes['walk']}@1.0", "start_frame": 1, "repeat": 2}]
    m["instances"][1]["animations"] = [{"clip": f"{codes['walk']}@1.0", "start_frame": 10, "travel": False}]
    r = create_scene(creator, m)
    assert r.status_code == 201, r.text
    s = r.json()
    v = creator.get(f"/api/v1/scenes/{s['id']}/versions/{s['versions'][0]['id']}").json()
    a, b = v["resolved"]["instances"]
    assert a["animations"][0]["source"] == "library" and a["animations"][0]["pin"]["kind"] == "animation"
    assert a["animations"][0]["pin"]["files"] == {"animation.blend": "d" * 64}
    assert a["animations"][0]["travel"] and not b["animations"][0]["travel"]
    assert a["animations"][0]["compat"]["stride_m"] == 0.57
    # the same animation version reused by both instances, queryable both ways
    use = creator.get(f"/api/v1/scenes/usage/{wvid}").json()
    assert [u["instance_key"] for u in use] == ["ali_school", "ali_winter"]
    with db() as sess, pytest.raises(Exception, match="cannot be deleted"):
        sess.execute(text("DELETE FROM animation_versions WHERE id = :i"), {"i": wvid})
    # incompatible: declared for adults only, and made for another rig profile
    m["instances"][0]["animations"] = [{"clip": f"{codes['adults']}@1.0", "start_frame": 1},
                                       {"clip": f"{codes['other']}@1.0", "start_frame": 1},
                                       {"clip": "ANIM-999@1.0", "start_frame": 1}]
    r = create_scene(creator, m)
    assert r.status_code == 422
    issues = r.json()["issues"]
    msgs = {i["path"]: i["message"] for i in issues}
    assert "declared for ['adult_average']" in msgs["instances[0].animations[0].clip"]
    assert "quadruped@1.0" in msgs["instances[0].animations[1].clip"]
    assert {i["code"] for i in issues} >= {"incompatible", "not_found"}


def test_openapi_has_the_animation_routes():
    from app.main import app

    paths = app.openapi()["paths"]
    assert {"/api/v1/animations", "/api/v1/animation-generators", "/api/v1/bone-maps", "/api/v1/uploads",
            "/api/v1/animations/{animation_id}/drafts/{draft_id}/generate",
            "/api/v1/animations/{animation_id}/versions/{version_id}/publish"} <= set(paths)
    assert not [n for n in app.openapi()["components"]["schemas"] if "__" in n]
