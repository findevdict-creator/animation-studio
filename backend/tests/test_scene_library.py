"""Scenes: manifests resolved against the registry, immutable versions, the compatibility
rules, the director's fit override, the build and reopen job records and the guards
(SRS 16.2, 16.3, Phase 5).

Library assets are published with the same service calls the publisher makes (files made
up); ``workers/tests/test_scene_e2e.py`` builds and reopens a scene with real Blender jobs."""

import uuid

import pytest
from app.assets import outfits as outfit_svc
from app.assets import scenes as svc
from app.models.scenes import SceneVersion
from sqlalchemy import text

from tests.test_accessory_library import acc_spec
from tests.test_accessory_library import published as published_accessory
from tests.test_outfit_library import built as built_outfit
from tests.test_outfit_library import (
    fit,
    outfit_spec,
    piece,
    published_character,
    record,
)


def published_outfit(api_as, db, cvid, *, spec=None, approve=True):
    """An outfit published at 1.0 with a fit on ``cvid`` (approved, or only verified)."""
    creator, reviewer = api_as("creator"), api_as("reviewer")
    o, ovid = built_outfit(creator, db, spec)
    job = fit(creator, o["id"], ovid, cvid).json()["job"]
    record(db, job["id"], ovid, cvid)
    base = f"/api/v1/outfits/{o['id']}"
    assert creator.post(f"{base}/drafts/{ovid}/submit").status_code == 200
    assert reviewer.post(f"{base}/versions/{ovid}/review", json={"decision": "approve"}).status_code == 200
    with db.begin() as s:
        v = outfit_svc.get_version(s, uuid.UUID(o["id"]), uuid.UUID(ovid), lock=True)
        prefix = outfit_svc.library_prefix(v, 1, 0)
        files = [{"role": f.role, "storage_key": prefix + f.storage_key.rsplit("/", 1)[-1], "sha256": f.sha256,
                  "size_bytes": f.size_bytes, "mime_type": f.mime_type} for f in v.entry.files]
        outfit_svc.complete_publish(s, v, major=1, minor=0, files=files,
                                    manifest={"storage_key": prefix + "manifest.json", "sha256": "f" * 64},
                                    published_by=None, expected_build=v.built_digest, job_id=uuid.UUID(job["id"]))
    if approve:
        r = reviewer.post(f"{base}/versions/{ovid}/fits/{cvid}/decision", json={"decision": "approve"})
        assert r.status_code == 200, r.text
    return o, ovid


@pytest.fixture
def cast(api_as, db):
    """One character, two outfits approved on it, glasses and a beanie."""
    c, cvid = published_character(api_as, db)
    school = published_outfit(api_as, db, cvid)
    winter = published_outfit(api_as, db, cvid, spec=outfit_spec(pieces=[
        piece("sweater"), piece("trousers"), piece("boots")]))
    glasses = published_accessory(api_as, db)
    beanie = published_accessory(api_as, db, acc_spec("beanie"))
    code = {k: api_as("reviewer").get(f"/api/v1/{path}/{x[0]['id']}").json()["code"]
            for k, path, x in (("school", "outfits", school), ("winter", "outfits", winter),
                               ("glasses", "accessories", glasses), ("beanie", "accessories", beanie))}
    code["ali"] = c["code"]
    return {"codes": code, "cvid": cvid, "school": school, "winter": winter, "glasses": glasses, "beanie": beanie,
            "character": c}


def manifest(codes, **kw):
    inst = [{"key": "ali_school", "character": {"code": codes["ali"], "version": "1.0"},
             "outfit": {"code": codes["school"], "version": "1.0"},
             "accessories": [{"accessory": {"code": codes["glasses"], "version": "1.0"}}],
             "transform": {"location": [-0.4, 0, 0]},
             "expressions": [{"frame": 1, "expression": "neutral"}, {"frame": 20, "expression": "happy"}],
             "animations": [{"clip": "wave", "start_frame": 1}],
             "dialogue": [{"start_frame": 24, "end_frame": 36, "text": "Hello!"}], "state": {"mood": "cheerful"}},
            {"key": "ali_winter", "character": {"code": codes["ali"], "version": "1.0"},
             "outfit": {"code": codes["winter"], "version": "1.0"},
             "accessories": [{"accessory": {"code": codes["beanie"], "version": "1.0"}}],
             "transform": {"location": [0.4, 0, 0]}, "animations": [{"clip": "CLIP-IDLE@0.1", "start_frame": 1}]}]
    return {"name": "Two Alis", "frame_start": 1, "frame_end": 48, "instances": inst, **kw}


def create(api, m, **kw):
    return api.post("/api/v1/scenes", json={"name": m["name"], "manifest": m, **kw})


def test_same_character_two_outfits_resolves_and_pins(api_as, db, cast):
    creator = api_as("creator")
    r = create(creator, manifest(cast["codes"]), tags=["demo"])
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["code"].startswith("SCENE-") and len(s["versions"]) == 1
    v = creator.get(f"/api/v1/scenes/{s['id']}/versions/{s['versions'][0]['id']}").json()
    assert v["number"] == 1 and v["build_status"] == "none" and v["verify_status"] == "none"
    rs = v["resolved"]
    a, b = rs["instances"]
    assert a["character"]["version_id"] == b["character"]["version_id"] == cast["cvid"]
    assert a["outfit"]["version_id"] == cast["school"][1] and b["outfit"]["version_id"] == cast["winter"][1]
    assert a["outfit_compatibility"]["status"] == "approved" and not a["outfit_compatibility"]["override"]
    assert a["character"]["path"] == f"assets/character/{cast['codes']['ali']}/1.0"
    assert set(a["character"]["files"]) == {"character.blend", "character.json"}
    assert a["accessories"][0]["attach_point"] == "face_eyes" and a["accessories"][0]["attach_bone"]
    assert a["animations"][0]["clip"] == "CLIP-WAVE@0.1"
    assert [p["key"] for p in v["pinned"]] == ["ali_school", "ali_winter"]
    assert v["pinned"][1]["accessories"][0]["attach_point"] == "head_top"
    # usage: the character version is pinned twice by this scene version
    use = creator.get(f"/api/v1/scenes/usage/{cast['cvid']}").json()
    assert [u["instance_key"] for u in use] == ["ali_school", "ali_winter"]
    listed = creator.get("/api/v1/scenes", params={"uses_version_id": cast["glasses"][1]}).json()["items"]
    assert [x["id"] for x in listed] == [s["id"]]
    # the library is untouched: nothing about the scene is on the character or outfits
    with db() as sess:
        n = sess.execute(text("SELECT count(*) FROM asset_versions_registry WHERE kind NOT IN "
                              "('character','outfit','accessory','animation','prop','environment')")).scalar_one()
        assert n == 0


def test_every_registry_problem_is_reported_at_once(api_as, db, cast):
    creator = api_as("creator")
    codes = cast["codes"]
    m = manifest(codes)
    m["instances"][0]["character"]["version"] = "9.0"
    m["instances"][1]["accessories"] = [{"accessory": {"code": codes["glasses"], "version": "1.0"},
                                         "attach_point": "wrist_L"}]
    m["instances"][1]["expressions"] = [{"frame": 5, "expression": "bored"}]
    m["instances"][1]["animations"] = [{"clip": "backflip", "start_frame": 1}]
    r = creator.post("/api/v1/scenes/validate", json={"manifest": m})
    assert r.status_code == 200 and not r.json()["ok"]
    codes_seen = {(i["path"], i["code"]) for i in r.json()["issues"]}
    assert ("instances[0].character", "not_found") in codes_seen
    assert ("instances[1].accessories[0]", "attach_point") in codes_seen
    assert ("instances[1].expressions[0].expression", "unknown_expression") in codes_seen
    assert ("instances[1].animations[0].clip", "unknown_clip") in codes_seen
    r = create(creator, m)
    assert r.status_code == 422 and r.json()["code"] == "invalid_manifest"


def test_outfit_must_be_approved_on_that_character_or_overridden(api_as, db, cast):
    creator, director = api_as("creator"), api_as("director")
    other = published_outfit(api_as, db, cast["cvid"], approve=False)
    m = manifest(cast["codes"])
    code = api_as("reviewer").get(f"/api/v1/outfits/{other[0]['id']}").json()["code"]
    m["instances"][0]["outfit"] = {"code": code, "version": "1.0"}
    r = creator.post("/api/v1/scenes/validate", json={"manifest": m}).json()
    assert not r["ok"] and [i["code"] for i in r["issues"]] == ["fit_not_approved"]
    # only a director may accept a verified fit, and that is recorded and audited
    assert create(creator, m, allow_verified_fits=True).status_code == 403
    r = create(director, m, allow_verified_fits=True)
    assert r.status_code == 201, r.text
    v = director.get(f"/api/v1/scenes/{r.json()['id']}/versions/{r.json()['versions'][0]['id']}").json()
    assert v["overrides"] == 1 and v["overrides_detail"][0]["instance"] == "ali_school"
    assert v["resolved"]["instances"][0]["outfit_compatibility"]["override"] is True
    with db() as s:
        assert s.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'scene.fit_override'")).scalar_one() >= 1
    # a character with no fit check for the outfit is refused, override or not
    other_char, _ = published_character(api_as, db)
    code2 = api_as("reviewer").get(f"/api/v1/characters/{other_char['id']}").json()["code"]
    m["instances"][0]["character"] = {"code": code2, "version": "1.0"}
    r = director.post("/api/v1/scenes/validate", json={"manifest": m, "allow_verified_fits": True}).json()
    assert any(i["code"] == "fit_not_approved" and "no fit check" in i["message"] for i in r["issues"])


def test_accessory_compatibility_rules(api_as, db, cast):
    creator = api_as("creator")
    codes = cast["codes"]
    capped = published_outfit(api_as, db, cast["cvid"], spec=outfit_spec(pieces=[
        piece("tshirt"), piece("shorts"), piece("sneakers"), piece("cap")]))
    cap_code = api_as("reviewer").get(f"/api/v1/outfits/{capped[0]['id']}").json()["code"]
    m = manifest(codes)
    m["instances"][1]["outfit"] = {"code": cap_code, "version": "1.0"}  # beanie on an outfit with a cap
    m["instances"][0]["accessories"].append({"accessory": {"code": codes["beanie"], "version": "1.0"},
                                             "attach_point": "face_eyes"})
    issues = creator.post("/api/v1/scenes/validate", json={"manifest": m}).json()["issues"]
    got = {(i["path"], i["code"]) for i in issues}
    assert ("instances[1].accessories[0]", "slot_conflict") in got
    assert ("instances[0].accessories[1]", "attach_point") in got
    narrow = published_accessory(api_as, db, acc_spec("watch", fit_profiles=["adult_average"]))
    ncode = api_as("reviewer").get(f"/api/v1/accessories/{narrow[0]['id']}").json()["code"]
    m = manifest(codes)
    m["instances"][0]["accessories"] = [{"accessory": {"code": ncode, "version": "1.0"}}]
    issues = creator.post("/api/v1/scenes/validate", json={"manifest": m}).json()["issues"]
    assert [i["code"] for i in issues] == ["fit_profile"]


def test_drafts_and_deprecated_pins(api_as, db, cast):
    creator, director = api_as("creator"), api_as("director")
    m = manifest(cast["codes"])
    s = create(creator, m).json()
    v1 = s["versions"][0]["id"]
    aid, avid = cast["glasses"][0]["id"], cast["glasses"][1]
    r = director.post(f"/api/v1/accessories/{aid}/versions/{avid}/deprecate", json={"reason": "old frames"})
    assert r.status_code == 200, r.text
    # a new scene cannot pin it; a new version of the scene that already pinned it can keep it
    assert create(creator, m).json()["issues"][0]["code"] == "deprecated"
    r = creator.post(f"/api/v1/scenes/{s['id']}/versions", json={"manifest": m})
    assert r.status_code == 422
    r = creator.post(f"/api/v1/scenes/{s['id']}/versions", json={"manifest": m, "based_on_version_id": v1})
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["number"] == 2 and v2["based_on_version_id"] == v1
    # a draft (unpublished) accessory can never be pinned
    from tests.test_accessory_library import built as built_accessory
    a, _vid, _ = built_accessory(creator, db)
    m["instances"][0]["accessories"] = [{"accessory": {"code": a["code"], "version": "1.0"}}]
    assert create(creator, m).json()["issues"][0]["code"] == "not_found"
    # reviewers can look but not build scenes
    assert create(api_as("reviewer"), manifest(cast["codes"])).status_code == 403


def fake_files(job_id, names):
    roles = {"scene.blend": "blend", "scene_report.json": "report", "frames.png": "preview",
             "thumbnail.png": "thumbnail", "reopen_report.json": "report", "reopened_frames.png": "preview"}
    return [{"role": roles[n], "storage_key": f"jobs/{job_id}/attempt-1/out/{n}", "sha256": ("a" if n ==
            "scene.blend" else "e") * 64, "size_bytes": 10, "mime_type": "application/octet-stream"} for n in names]


def test_assemble_then_reopen_records_and_freezes(api_as, db, cast, dispatcher):
    creator = api_as("creator")
    s = create(creator, manifest(cast["codes"])).json()
    base = f"/api/v1/scenes/{s['id']}/versions/{s['versions'][0]['id']}"
    assert creator.post(f"{base}/reopen", json={}).json()["code"] == "not_built"
    r = creator.post(f"{base}/assemble", json={"previews": {"width": 320, "height": 180}})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert job["type"] == "assemble_scene" and r.json()["version"]["build_status"] == "queued"
    assert job["params"]["resolved"]["manifest_digest"] == r.json()["version"]["manifest_digest"]
    assert creator.post(f"{base}/assemble", json={}).json()["code"] == "build_in_progress"
    vid = uuid.UUID(s["versions"][0]["id"])
    with db.begin() as sess:
        digest = sess.get(SceneVersion, vid).manifest_digest
        out = svc.record_build(sess, uuid.UUID(job["id"]), vid,
                               fake_files(job["id"], ["scene.blend", "scene_report.json", "frames.png",
                                                      "thumbnail.png"]),
                               {"ok": True, "scene_blend_sha256": "a" * 64,
                                "report": {"manifest_digest": digest, "ok": True}}, "5.2.3")
    assert out["build_status"] == "built"
    v = creator.get(base).json()
    assert v["build_status"] == "built" and v["scene_blend_sha256"] == "a" * 64 and v["thumbnail_url"]
    assert {f["name"] for f in v["build_files"]} >= {"scene.blend", "frames.png"}
    assert creator.post(f"{base}/assemble", json={}).json()["code"] == "already_built"
    assert creator.get(f"{base}/files/scene.blend", follow_redirects=False).status_code == 302
    r = creator.post(f"{base}/reopen", json={})
    assert r.status_code == 202 and r.json()["job"]["params"]["scene_blend_sha256"] == "a" * 64
    vjob = r.json()["job"]
    with db.begin() as sess:
        out = svc.record_verify(sess, uuid.UUID(vjob["id"]), vid,
                                fake_files(vjob["id"], ["reopen_report.json", "reopened_frames.png"]),
                                {"ok": True, "report": {"ok": True, "scene_blend_sha256": "a" * 64}})
    assert out["verify_status"] == "verified"
    assert creator.get(base).json()["verify_status"] == "verified"
    # guards: a built scene.blend, the manifest and the pins never change
    with db() as sess, pytest.raises(Exception, match="is built"):
        sess.execute(text("UPDATE scene_versions SET scene_blend_sha256 = :x WHERE id = :i"), {"x": "b" * 64, "i": vid})
    with db() as sess, pytest.raises(Exception, match="immutable"):
        sess.execute(text("UPDATE scene_versions SET manifest = '{}' WHERE id = :i"), {"i": vid})
    with db() as sess, pytest.raises(Exception, match="cannot be deleted"):
        sess.execute(text("DELETE FROM scene_versions WHERE id = :i"), {"i": vid})
    with db() as sess, pytest.raises(Exception, match="never change"):
        sess.execute(text("DELETE FROM scene_instances WHERE scene_version_id = :i"), {"i": vid})
    with db() as sess, pytest.raises(Exception, match="cannot be deleted"):
        sess.execute(text("DELETE FROM character_versions WHERE id = :i"), {"i": cast["cvid"]})


def test_a_failed_build_can_be_queued_again(api_as, db, cast, dispatcher):
    creator = api_as("creator")
    s = create(creator, manifest(cast["codes"])).json()
    base = f"/api/v1/scenes/{s['id']}/versions/{s['versions'][0]['id']}"
    job = creator.post(f"{base}/assemble", json={}).json()["job"]
    with db.begin() as sess:
        sess.execute(text("UPDATE jobs SET status = 'failed' WHERE id = :j"), {"j": job["id"]})
    assert creator.get(base).json()["build_status"] == "failed"
    r = creator.post(f"{base}/assemble", json={})
    assert r.status_code == 202 and r.json()["job"]["id"] != job["id"]
    # the first job's late result is ignored
    with db.begin() as sess:
        late = svc.record_build(sess, uuid.UUID(job["id"]), uuid.UUID(s["versions"][0]["id"]), [], {}, None)
    assert late["recorded"] is False


def test_clip_catalog(api_as):
    clips = api_as("reviewer").get("/api/v1/animation-clips").json()
    assert {c["key"] for c in clips} >= {"idle", "wave", "walk"}
