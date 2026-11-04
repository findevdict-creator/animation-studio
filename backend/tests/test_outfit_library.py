"""Outfit Library: identity and scope, drafts, builds, the fit gate on submit, review, the
compatibility registry and the database guards (SRS 7, Phase 4).

Builds and fit checks are recorded with the same service calls the worker makes after a
real Blender job (``workers/tests/test_outfit_library_e2e.py`` runs those jobs for real)."""

import uuid

import pytest
from sqlalchemy import text

from app.assets import characters as char_svc
from app.assets import outfits as svc
from app.models.characters import CharacterVersion
from app.models.outfits import OutfitCompatibility, OutfitVersion
from studio_contracts.outfit import get_garment
from tests.test_character_library import create as create_character
from tests.test_character_library import fake_build as fake_character_build
from tests.test_character_library import generate as generate_character

ROLES = {"blend": "outfit.blend", "spec": "outfit.json", "report": "fit_report.json", "thumbnail": "thumbnail.png",
         "preview": "poses.png", "glb": "preview.glb"}


def piece(key, **kw):
    g = get_garment(key)
    return {"slot": g.slot, "garment": {"id": g.id, "version": g.version}, **kw}


def outfit_spec(**kw):
    return {"fit_profiles": ["child_average"],
            "pieces": [piece("tshirt", material={"color": "royal_blue"}), piece("shorts"), piece("sneakers")], **kw}


def create(api, spec=None, name=None):
    r = api.post("/api/v1/outfits", json={"name": name or f"School {uuid.uuid4().hex[:6]}", "tags": ["school"],
                                           "spec": spec or outfit_spec()})
    assert r.status_code == 201, r.text
    return r.json()


def fake_build(db, version_id, job_id, *, ok=True, blend_sha="d" * 64):
    uploaded = [{"role": role, "storage_key": f"jobs/{job_id}/attempt-1/out/{name}",
                 "sha256": blend_sha if role == "blend" else "e" * 64, "size_bytes": 10,
                 "mime_type": "application/octet-stream"} for role, name in ROLES.items()]
    with db.begin() as s:
        v = s.get(OutfitVersion, uuid.UUID(version_id))
        report = {"ok": ok, "failed": [] if ok else ["no_clipping:lower_shorts"], "outfit_blend_sha256": blend_sha,
                  "checks": []}
        return svc.attach_build(s, v, uuid.UUID(job_id), uploaded,
                                {"resolved_digest": v.spec_digest, "report": report, "fit": {"ok": ok}}, "5.2.3")


def generate(api, oid, vid):
    r = api.post(f"/api/v1/outfits/{oid}/drafts/{vid}/generate", json={"previews": {"resolution": 96}})
    assert r.status_code == 202, r.text
    return r.json()


def built(api, db, spec=None, ok=True):
    o = create(api, spec)
    vid = o["versions"][0]["id"]
    job = generate(api, o["id"], vid)["job"]
    assert fake_build(db, vid, job["id"], ok=ok)["attached"]
    return o, vid


def published_character(api_as, db, spec=None):
    """A character with one published version (the publisher's final transaction, files made up)."""
    creator = api_as("creator")
    c = create_character(creator, spec=spec)
    cid, vid = c["id"], c["versions"][0]["id"]
    job = generate_character(creator, cid, vid)["job"]
    fake_character_build(db, vid, job["id"])
    assert creator.post(f"/api/v1/characters/{cid}/drafts/{vid}/submit").status_code == 200
    assert api_as("reviewer").post(f"/api/v1/characters/{cid}/versions/{vid}/review",
                                   json={"decision": "approve"}).status_code == 200
    with db.begin() as s:
        v = char_svc.get_version(s, uuid.UUID(cid), uuid.UUID(vid), lock=True)
        prefix = f"library/characters/{cid}/versions/1.0/"
        files = [{"role": "blend", "storage_key": prefix + "character.blend", "sha256": "b" * 64, "size_bytes": 10,
                  "mime_type": "application/x-blender"},
                 {"role": "spec", "storage_key": prefix + "character.json", "sha256": "c" * 64, "size_bytes": 10,
                  "mime_type": "application/json"}]
        char_svc.complete_publish(s, v, major=1, minor=0, files=files,
                                  manifest={"storage_key": prefix + "manifest.json", "sha256": "f" * 64},
                                  published_by=None, expected_build=v.built_digest, job_id=uuid.UUID(job["id"]))
    return c, vid


def fit(api, oid, ovid, cvid):
    r = api.post(f"/api/v1/outfits/{oid}/versions/{ovid}/fits", json={"character_version_id": cvid})
    return r


def record(db, job_id, ovid, cvid, ok=True, blend_sha="d" * 64, declared=True):
    report = {"ok": ok, "failed": [] if ok else ["no_clipping:lower_trousers"], "outfit_blend_sha256": blend_sha,
              "wearer": {"kind": "character", "fit_profile": "child_average", "character_blend_sha256": "b" * 64},
              "fit_profile_declared": declared}
    uploaded = [{"role": "report", "storage_key": f"jobs/{job_id}/attempt-1/out/fit_report.json", "sha256": "a" * 64,
                 "size_bytes": 5, "mime_type": "application/json"}]
    with db.begin() as s:
        return svc.record_fit(s, uuid.UUID(job_id), uuid.UUID(ovid), uuid.UUID(cvid), uploaded,
                              {"ok": ok, "report": report})


# ---------------------------------------------------------------- identity and drafts
def test_create_outfit_and_first_draft(api_as):
    o = create(api_as("creator"))
    assert o["code"].startswith("OUTFIT-") and o["scope"] == "body_profile" and o["owner_character_id"] is None
    (v,) = o["versions"]
    assert v["status"] == "draft" and v["fit_profiles"] == ["child_average"]
    assert v["slots"] == ["footwear", "lower", "upper"]
    detail = api_as("reviewer").get(f"/api/v1/outfits/{o['id']}/versions/{v['id']}").json()
    assert detail["ready_for_review"] == ["no build yet: run generate", "missing files: blend, spec, report, thumbnail, glb"]
    assert detail["rig_profile_ref"] == "cartoon_humanoid@1.0" and detail["catalog_ref"]


def test_invalid_specs_are_refused_with_every_issue(api_as):
    bad = outfit_spec(pieces=[piece("tshirt"), piece("sweater"), piece("cap", params={"crown": 9})])
    r = api_as("creator").post("/api/v1/outfits", json={"name": "Bad", "spec": bad})
    assert r.status_code == 422 and r.json()["code"] == "invalid_spec"
    assert len(r.json()["issues"]) >= 2


def test_character_scope_needs_a_real_owner_and_never_changes(api_as, db):
    creator = api_as("creator")
    r = creator.post("/api/v1/outfits", json={"name": "Ali's", "spec": outfit_spec(scope="character",
                                                                                   owner_character="CHAR-999")})
    assert r.status_code == 422 and r.json()["code"] == "unknown_owner_character"
    c = create_character(creator)
    o = create(creator, outfit_spec(scope="character", owner_character=c["code"]))
    assert o["scope"] == "character" and o["owner_character_id"] == c["id"]
    vid = o["versions"][0]["id"]
    r = creator.patch(f"/api/v1/outfits/{o['id']}/drafts/{vid}", json={"spec": outfit_spec()})
    assert r.status_code == 422 and r.json()["code"] == "scope_changed"
    with db() as s, pytest.raises(Exception, match="scope and owner never change"):
        s.execute(text("UPDATE outfits SET scope = 'body_profile', owner_character_id = NULL WHERE id = :i"),
                  {"i": o["id"]})
    listed = api_as("reviewer").get("/api/v1/outfits", params={"owner_character_id": c["id"]}).json()["items"]
    assert [x["id"] for x in listed] == [o["id"]]


def test_edit_draft_and_gallery_filters(api_as):
    creator = api_as("creator")
    o = create(creator, name=f"Winter {uuid.uuid4().hex[:6]}")
    vid = o["versions"][0]["id"]
    etag = creator.get(f"/api/v1/outfits/{o['id']}/versions/{vid}").headers["ETag"]
    spec = outfit_spec(pieces=[piece("sweater"), piece("trousers"), piece("boots")])
    r = creator.patch(f"/api/v1/outfits/{o['id']}/drafts/{vid}", json={"spec": spec}, headers={"If-Match": etag})
    assert r.status_code == 200 and r.json()["slots"] == ["footwear", "lower", "upper"]
    r = creator.patch(f"/api/v1/outfits/{o['id']}/drafts/{vid}", json={"spec": spec}, headers={"If-Match": etag})
    assert r.status_code == 412
    viewer = api_as("reviewer")
    assert o["id"] in {x["id"] for x in viewer.get("/api/v1/outfits", params={"q": "Winter"}).json()["items"]}
    assert o["id"] in {x["id"] for x in viewer.get("/api/v1/outfits", params={"slot": "footwear"}).json()["items"]}
    assert o["id"] not in {x["id"] for x in viewer.get("/api/v1/outfits", params={"fit_profile": "adult_average"}).json()["items"]}


# ---------------------------------------------------------------- builds and the fit gate
def test_a_failed_fit_check_blocks_submit(api_as, db):
    creator = api_as("creator")
    o, vid = built(creator, db, ok=False)
    r = creator.post(f"/api/v1/outfits/{o['id']}/drafts/{vid}/submit")
    assert r.status_code == 409 and r.json()["code"] == "not_ready_for_review"
    assert any("no_clipping:lower_shorts" in p for p in r.json()["problems"])
    # a rebuild that passes clears it
    job = generate(creator, o["id"], vid)["job"]
    fake_build(db, vid, job["id"], ok=True)
    assert creator.post(f"/api/v1/outfits/{o['id']}/drafts/{vid}/submit").json()["status"] == "review"


def test_stale_and_superseded_builds(api_as, db):
    creator = api_as("creator")
    o, vid = built(creator, db)
    old_job = generate(creator, o["id"], vid)["job"]
    new_job = generate(creator, o["id"], vid)["job"]
    assert fake_build(db, vid, old_job["id"]) == {"attached": False, "reason": "a newer build was requested"}
    assert fake_build(db, vid, new_job["id"])["attached"]
    creator.patch(f"/api/v1/outfits/{o['id']}/drafts/{vid}", json={"spec": outfit_spec(seed=7)})
    problems = creator.get(f"/api/v1/outfits/{o['id']}/versions/{vid}").json()["ready_for_review"]
    assert problems == ["the spec changed since the last build: run generate again"]


def test_review_cycle_and_review_freeze(api_as, db):
    creator, reviewer = api_as("creator"), api_as("reviewer")
    o, vid = built(creator, db)
    base = f"/api/v1/outfits/{o['id']}"
    assert creator.post(f"{base}/drafts/{vid}/submit").status_code == 200
    with db() as s, pytest.raises(Exception, match="in review, its build cannot change"):
        s.execute(text("UPDATE outfit_versions SET spec_digest = 'x' WHERE id = :i"), {"i": vid})
    r = reviewer.post(f"{base}/versions/{vid}/review", json={"decision": "reject", "notes": "shorts too long"})
    assert r.json()["status"] == "draft" and r.json()["review_notes"] == "shorts too long"
    assert creator.post(f"{base}/drafts/{vid}/submit").status_code == 200
    assert reviewer.post(f"{base}/versions/{vid}/review", json={"decision": "approve"}).json()["review_decision"] == "approve"
    assert creator.post(f"{base}/versions/{vid}/publish").status_code == 403
    r = api_as("director").post(f"{base}/versions/{vid}/publish", json={"bump": "minor"})
    assert r.status_code == 202 and r.json()["job"]["type"] == "publish_outfit" and r.json()["job"]["queue"] == "publish"


def test_discarding_a_draft(api_as, db):
    creator = api_as("creator")
    o, vid = built(creator, db)
    assert creator.delete(f"/api/v1/outfits/{o['id']}/drafts/{vid}").status_code == 204
    assert api_as("reviewer").get(f"/api/v1/outfits/{o['id']}/versions/{vid}").status_code == 404


# ---------------------------------------------------------------- compatibility registry
def test_fits_need_a_build_and_a_published_character(api_as, db):
    creator = api_as("creator")
    o = create(creator)
    ovid = o["versions"][0]["id"]
    c, cvid = published_character(api_as, db)
    assert fit(creator, o["id"], ovid, cvid).json()["code"] == "no_build"
    job = generate(creator, o["id"], ovid)["job"]
    fake_build(db, ovid, job["id"])
    draft_char = create_character(creator)
    r = fit(creator, o["id"], ovid, draft_char["versions"][0]["id"])
    assert r.status_code == 409 and r.json()["code"] == "character_not_published"
    r = fit(creator, o["id"], ovid, cvid)
    assert r.status_code == 202 and r.json()["fit"]["status"] == "candidate" and r.json()["job"]["type"] == "fit_outfit"
    assert r.json()["fit"]["character"]["code"] == c["code"]


def test_fit_verdicts_approval_and_revocation(api_as, db):
    creator, reviewer = api_as("creator"), api_as("reviewer")
    o, ovid = built(creator, db)
    _, cvid = published_character(api_as, db)
    base = f"/api/v1/outfits/{o['id']}/versions/{ovid}/fits"
    job = fit(creator, o["id"], ovid, cvid).json()["job"]
    assert record(db, job["id"], ovid, cvid, ok=False)["status"] == "failed"
    assert reviewer.post(f"{base}/{cvid}/decision", json={"decision": "approve"}).json()["code"] == "fit_not_verified"
    # check again: a newer job replaces the verdict; the old job's late result is ignored
    stale = job
    job = fit(creator, o["id"], ovid, cvid).json()["job"]
    assert record(db, stale["id"], ovid, cvid)["recorded"] is False
    assert record(db, job["id"], ovid, cvid)["status"] == "verified"
    detail = api_as("reviewer").get(f"{base}/{cvid}").json()
    assert detail["status"] == "verified" and detail["report"]["ok"] and detail["current"]
    assert detail["files"][0]["name"] == "fit_report.json"
    r = reviewer.post(f"{base}/{cvid}/decision", json={"decision": "approve", "notes": "looks right"})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["decided_by"]
    assert fit(creator, o["id"], ovid, cvid).json()["code"] == "fit_approved"
    with db() as s, pytest.raises(Exception, match="evidence cannot change"):
        s.execute(text("UPDATE outfit_compatibility SET report = '{}' WHERE outfit_version_id = :o"), {"o": ovid})
    wardrobe = api_as("reviewer").get(f"/api/v1/characters/{detail['character']['id']}/versions/{cvid}/outfits",
                                    params={"status": "approved"}).json()
    assert [w["outfit_version_id"] for w in wardrobe] == [ovid]
    assert reviewer.post(f"{base}/{cvid}/decision", json={"decision": "revoke"}).json()["status"] == "revoked"
    assert creator.post(f"{base}/{cvid}/decision", json={"decision": "approve"}).status_code == 403


def test_the_registry_refuses_an_approval_without_a_verified_fit(api_as, db):
    creator = api_as("creator")
    o, ovid = built(creator, db)
    _, cvid = published_character(api_as, db)
    fit(creator, o["id"], ovid, cvid)
    with db() as s, pytest.raises(Exception, match="only a verified fit can be approved"):
        s.execute(text("UPDATE outfit_compatibility SET status = 'approved' WHERE outfit_version_id = :o"), {"o": ovid})


def test_a_rebuild_drops_the_drafts_fits(api_as, db):
    creator = api_as("creator")
    o, ovid = built(creator, db)
    _, cvid = published_character(api_as, db)
    job = fit(creator, o["id"], ovid, cvid).json()["job"]
    record(db, job["id"], ovid, cvid)
    job = generate(creator, o["id"], ovid)["job"]
    fake_build(db, ovid, job["id"], blend_sha="9" * 64)
    with db() as s:
        assert s.query(OutfitCompatibility).filter_by(outfit_version_id=uuid.UUID(ovid)).count() == 0


def test_character_scope_outfit_needs_a_verified_fit_on_its_owner(api_as, db):
    creator = api_as("creator")
    c, cvid = published_character(api_as, db)
    other, other_vid = published_character(api_as, db)
    o, ovid = built(creator, db, spec=outfit_spec(scope="character", owner_character=c["code"]))
    base = f"/api/v1/outfits/{o['id']}"
    r = creator.post(f"{base}/drafts/{ovid}/submit")
    assert r.status_code == 409 and any("owner" in p for p in r.json()["problems"])
    assert fit(creator, o["id"], ovid, other_vid).json()["code"] == "not_the_owner"
    job = fit(creator, o["id"], ovid, cvid).json()["job"]
    record(db, job["id"], ovid, cvid, ok=False)
    assert creator.post(f"{base}/drafts/{ovid}/submit").status_code == 409
    job = fit(creator, o["id"], ovid, cvid).json()["job"]
    record(db, job["id"], ovid, cvid, ok=True)
    assert creator.post(f"{base}/drafts/{ovid}/submit").json()["status"] == "review"


def test_published_outfit_versions_and_their_fits_are_frozen(api_as, db):
    creator, reviewer = api_as("creator"), api_as("reviewer")
    o, ovid = built(creator, db)
    _, cvid = published_character(api_as, db)
    job = fit(creator, o["id"], ovid, cvid).json()["job"]
    record(db, job["id"], ovid, cvid)
    base = f"/api/v1/outfits/{o['id']}"
    creator.post(f"{base}/drafts/{ovid}/submit")
    reviewer.post(f"{base}/versions/{ovid}/review", json={"decision": "approve"})
    with db.begin() as s:
        v = svc.get_version(s, uuid.UUID(o["id"]), uuid.UUID(ovid), lock=True)
        prefix = svc.library_prefix(v, 1, 0)
        files = [{"role": f.role, "storage_key": prefix + f.storage_key.rsplit("/", 1)[-1], "sha256": f.sha256,
                  "size_bytes": f.size_bytes, "mime_type": f.mime_type} for f in v.entry.files]
        svc.complete_publish(s, v, major=1, minor=0, files=files,
                             manifest={"storage_key": prefix + "manifest.json", "sha256": "f" * 64},
                             published_by=None, expected_build=v.built_digest, job_id=uuid.UUID(job["id"]))
    detail = api_as("reviewer").get(f"{base}").json()
    assert detail["latest_published"] == "1.0" and detail["recommended_version"] == "1.0"
    assert creator.patch(f"{base}/drafts/{ovid}", json={"spec": outfit_spec()}).json()["code"] == "not_a_draft"
    with db() as s, pytest.raises(Exception, match="cannot be changed"):
        s.execute(text("UPDATE outfit_versions SET spec = '{}' WHERE id = :i"), {"i": ovid})
    with db() as s, pytest.raises(Exception, match="cannot be deleted"):
        s.execute(text("DELETE FROM outfit_versions WHERE id = :i"), {"i": ovid})
    with db() as s, pytest.raises(Exception, match="are kept"):
        s.execute(text("DELETE FROM outfit_compatibility WHERE outfit_version_id = :i"), {"i": ovid})
    # a new draft based on the published version is the only way to change it
    r = creator.post(f"{base}/drafts", json={"based_on_version_id": ovid})
    assert r.status_code == 201 and r.json()["based_on_version_id"] == ovid
    with db() as s:  # the character is untouched by all of this
        cv = s.get(CharacterVersion, uuid.UUID(cvid))
        assert cv.status == "published" and {f.role for f in cv.entry.files} == {"blend", "spec"}



def test_openapi_schema_names_stay_unique():
    """The web app's types are generated from these names; a clash would rename both."""
    from app.main import app

    assert not [n for n in app.openapi()["components"]["schemas"] if "__" in n]
