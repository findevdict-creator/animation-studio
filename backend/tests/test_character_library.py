"""Character Library: identity, drafts, builds, review, and the database guards (SRS Phase 3).

A build is recorded here with ``attach_build`` and made-up file rows, which is exactly what the
worker does after a real Blender job (``workers/tests/test_generate_character.py`` runs that).
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.assets import characters as svc
from app.models.characters import CharacterVersion

SPEC = {
    "body": {"age_appearance": "child", "height_m": 1.28},
    "hair": {"style": "short", "color": "black"},
}
ROLES = {"blend": "character.blend", "spec": "character.json", "report": "validation.json",
         "thumbnail": "thumbnail.png", "glb": "preview.glb", "turntable": "turntable.mp4"}


def _name(prefix="Ali"):
    return f"{prefix} {uuid.uuid4().hex[:6]}"


def create(api, name=None, tags=("child",), spec=None):
    r = api.post("/api/v1/characters", json={"name": name or _name(), "description": "a curious boy",
                                              "tags": list(tags), "spec": spec or SPEC})
    assert r.status_code == 201, r.text
    return r.json()


def fake_build(db, version_id, job_id, *, ok=True, blend_sha="b" * 64, report_sha=None):
    """What the worker records after a generate job, without running Blender."""
    uploaded = [{"role": role, "storage_key": f"jobs/{job_id}/attempt-1/out/{name}",
                 "sha256": blend_sha if role == "blend" else "c" * 64, "size_bytes": 10,
                 "mime_type": "application/octet-stream"} for role, name in ROLES.items()]
    with db.begin() as s:
        v = s.get(CharacterVersion, uuid.UUID(version_id))
        data = {"resolved_digest": v.spec_digest, "height_m": 1.28,
                "report": {"ok": ok, "blend_sha256": report_sha or blend_sha, "checks": []}}
        return svc.attach_build(s, v, uuid.UUID(job_id), uploaded, data, "5.2.3")


def generate(api, cid, vid):
    r = api.post(f"/api/v1/characters/{cid}/drafts/{vid}/generate", json={"previews": {"resolution": 128}})
    assert r.status_code == 202, r.text
    return r.json()


def built_draft(api, db, cid=None):
    c = create(api) if cid is None else api.get(f"/api/v1/characters/{cid}").json()
    vid = c["versions"][0]["id"]
    job = generate(api, c["id"], vid)["job"]
    assert fake_build(db, vid, job["id"])["attached"]
    return c, vid


# ---------------------------------------------------------------- identity
def test_create_character_and_first_draft(api_as):
    creator = api_as("creator")
    c = create(creator, tags=["child", "family", "child"])
    assert c["code"].startswith("CHAR-") and c["tags"] == ["child", "family"]
    assert c["counts"] == {"draft": 1} and c["recommended_version_id"] is None
    (v,) = c["versions"]
    assert v["status"] == "draft" and v["version_label"] is None and v["fit_profile"] == "child_average"
    detail = creator.get(f"/api/v1/characters/{c['id']}/versions/{v['id']}")
    assert detail.status_code == 200 and detail.headers["ETag"]
    body = detail.json()
    assert body["spec"]["name"] == c["name"]  # the spec's name defaults to the character's
    assert body["ready_for_review"] == ["no build yet: run generate", "the last build did not pass validation",
                                        "missing files: blend, spec, report, thumbnail, glb"]


def test_invalid_spec_lists_every_issue(api_as):
    r = api_as("creator").post("/api/v1/characters", json={
        "name": "Bad", "spec": {"body": {"age_appearance": "toddler", "height_m": 9}, "hair": {"style": "mohawk"}}})
    assert r.status_code == 422 and r.json()["code"] == "invalid_spec"
    paths = {i["path"] for i in r.json()["issues"]}
    assert {"body.age_appearance", "hair.style"} <= paths
    assert api_as("reviewer").post("/api/v1/characters", json={"name": "X", "spec": SPEC}).status_code == 403
    assert api_as("creator").post("/api/v1/characters", json={"name": "X", "tags": ["Bad Tag"],
                                                               "spec": SPEC}).json()["code"] == "invalid_tag"


def test_gallery_search_and_filters(api_as):
    creator = api_as("creator")
    marker = uuid.uuid4().hex[:8]
    a = create(creator, name=f"Zed {marker}", tags=["villain"])
    b = create(creator, name=f"Yan {marker}", tags=["hero", "child"],
               spec={**SPEC, "body": {"age_appearance": "adult", "height_m": 1.7}})
    page = creator.get("/api/v1/characters", params={"q": marker}).json()
    assert [c["id"] for c in page["items"]] == [b["id"], a["id"]]  # newest first
    assert [c["id"] for c in creator.get("/api/v1/characters", params={"q": marker, "tag": "hero"}).json()["items"]] == [b["id"]]
    assert [c["id"] for c in creator.get("/api/v1/characters",
                                         params={"q": marker, "fit_profile": "child_average"}).json()["items"]] == [a["id"]]
    assert creator.get("/api/v1/characters", params={"q": marker, "status": "published"}).json()["items"] == []
    assert creator.get("/api/v1/characters", params={"q": a["code"]}).json()["items"][0]["id"] == a["id"]
    one = creator.get("/api/v1/characters", params={"q": marker, "limit": 1}).json()
    rest = creator.get("/api/v1/characters", params={"q": marker, "limit": 1, "cursor": one["next_cursor"]}).json()
    assert [c["id"] for c in one["items"] + rest["items"]] == [b["id"], a["id"]] and rest["next_cursor"] is None


def test_identity_edits(api_as):
    creator, director = api_as("creator"), api_as("director")
    c = create(creator)
    r = director.patch(f"/api/v1/characters/{c['id']}", json={"name": "Ali Prime", "tags": ["lead"]})
    assert r.status_code == 200 and r.json()["name"] == "Ali Prime" and r.json()["tags"] == ["lead"]
    draft = c["versions"][0]["id"]
    r = director.patch(f"/api/v1/characters/{c['id']}", json={"recommended_version_id": draft})
    assert r.status_code == 409 and r.json()["code"] == "not_published"
    assert api_as("reviewer").patch(f"/api/v1/characters/{c['id']}", json={"name": "x"}).status_code == 403


# ---------------------------------------------------------------- drafts and builds
def test_generate_links_the_job_to_the_draft(api, dispatcher):
    c = create(api)
    vid = c["versions"][0]["id"]
    out = generate(api, c["id"], vid)
    job = out["job"]
    assert job["type"] == "generate_character" and job["params"]["draft_version_id"] == vid
    assert job["params"]["spec"]["name"] == c["name"] and job["params"]["previews"]["resolution"] == 128
    assert out["version"]["generate_job_id"] == job["id"]
    assert dispatcher.sent[-1] == (job["id"], "blender", 0)


def test_build_submit_review_cycle(api_as, db):
    creator, reviewer = api_as("creator"), api_as("reviewer")
    c, vid = built_draft(creator, db)
    base = f"/api/v1/characters/{c['id']}"
    v = creator.get(f"{base}/versions/{vid}").json()
    assert v["ready_for_review"] == [] and v["built_digest"] == v["spec_digest"]
    assert sorted(f["name"] for f in v["files"]) == sorted(ROLES.values())

    # editing the spec makes the build stale, so it cannot be submitted until rebuilt
    etag = creator.get(f"{base}/versions/{vid}").headers["ETag"]
    taller = {**v["spec"], "body": {**v["spec"]["body"], "height_m": 1.3}}
    r = creator.patch(f"{base}/drafts/{vid}", json={"spec": taller}, headers={"If-Match": etag})
    assert r.status_code == 200 and r.json()["ready_for_review"] == [
        "the spec changed since the last build: run generate again"]
    stale = creator.patch(f"{base}/drafts/{vid}", json={"spec": taller}, headers={"If-Match": etag})
    assert stale.status_code == 412 and stale.json()["code"] == "stale_draft"
    r = creator.post(f"{base}/drafts/{vid}/submit")
    assert r.status_code == 409 and r.json()["code"] == "not_ready_for_review"
    job = generate(creator, c["id"], vid)["job"]
    fake_build(db, vid, job["id"])

    r = creator.post(f"{base}/drafts/{vid}/submit")
    assert r.status_code == 200 and r.json()["status"] == "review"
    # in review the build is frozen: no edits, no rebuild, no discard
    assert creator.patch(f"{base}/drafts/{vid}", json={"spec": SPEC}).json()["code"] == "not_a_draft"
    assert creator.post(f"{base}/drafts/{vid}/generate").json()["code"] == "not_a_draft"
    assert creator.delete(f"{base}/drafts/{vid}").json()["code"] == "not_a_draft"
    assert creator.post(f"{base}/versions/{vid}/review", json={"decision": "approve"}).status_code == 403

    r = reviewer.post(f"{base}/versions/{vid}/review", json={"decision": "reject", "notes": "ears too big"})
    assert r.status_code == 200 and r.json()["status"] == "draft" and r.json()["review_notes"] == "ears too big"
    assert creator.post(f"{base}/drafts/{vid}/submit").status_code == 200
    r = reviewer.post(f"{base}/versions/{vid}/review", json={"decision": "approve", "notes": "good"})
    assert r.status_code == 200 and r.json()["status"] == "review" and r.json()["review_decision"] == "approve"
    assert r.json()["reviewed_by"] is not None
    r = api_as("director").post(f"{base}/versions/{vid}/deprecate")
    assert r.status_code == 409 and r.json()["code"] == "invalid_version_status"


def test_failed_or_mismatched_build_cannot_be_submitted(api, db):
    c = create(api)
    vid, base = c["versions"][0]["id"], f"/api/v1/characters/{c['id']}"
    fake_build(db, vid, generate(api, c["id"], vid)["job"]["id"], ok=False)
    assert api.get(f"{base}/versions/{vid}").json()["ready_for_review"] == ["the last build did not pass validation"]
    fake_build(db, vid, generate(api, c["id"], vid)["job"]["id"], report_sha="d" * 64)
    assert api.get(f"{base}/versions/{vid}").json()["ready_for_review"] == [
        "the validation report does not describe this character.blend"]


def test_only_the_latest_build_is_attached(api, db):
    c = create(api)
    vid = c["versions"][0]["id"]
    first = generate(api, c["id"], vid)["job"]["id"]
    second = generate(api, c["id"], vid)["job"]["id"]
    assert fake_build(db, vid, first) == {"attached": False, "reason": "a newer build was requested"}
    assert fake_build(db, vid, second)["attached"]


def test_new_draft_copies_the_source_spec_and_discard(api, db):
    c, vid = built_draft(api, db)
    base = f"/api/v1/characters/{c['id']}"
    r = api.post(f"{base}/drafts", json={"based_on_version_id": vid})
    assert r.status_code == 201, r.text
    d = r.json()
    source = api.get(f"{base}/versions/{vid}").json()
    assert d["based_on_version_id"] == vid and d["spec"] == source["spec"] and d["files"] == []
    assert api.delete(f"{base}/drafts/{vid}").json()["code"] == "draft_referenced"
    assert api.delete(f"{base}/drafts/{d['id']}").status_code == 204
    assert api.get(f"{base}/versions/{d['id']}").status_code == 404
    assert api.post(f"{base}/drafts", json={}).json()["code"] == "spec_required"
    other = create(api)
    r = api.post(f"{base}/drafts", json={"based_on_version_id": other["versions"][0]["id"]})
    assert r.status_code == 404  # a version of another character is not a source


def test_file_download_is_presigned(api, db):
    c, vid = built_draft(api, db)
    r = api.get(f"/api/v1/characters/{c['id']}/versions/{vid}/files/preview.glb", follow_redirects=False)
    assert r.status_code == 302 and "preview.glb" in r.headers["location"] and "Signature" in r.headers["location"]
    r = api.get(f"/api/v1/characters/{c['id']}/versions/{vid}/files/preview.glb", params={"redirect": False})
    assert r.status_code == 200 and r.json()["expires_in_s"] > 0
    assert api.get(f"/api/v1/characters/{c['id']}/versions/{vid}/files/nope.txt").status_code == 404
    page = api.get("/api/v1/characters", params={"q": c["code"]}).json()
    assert "thumbnail.png" in page["items"][0]["thumbnail_url"]


# ---------------------------------------------------------------- database guards
def _version(session, status):
    c, v = svc.create_character(session, name=_name("Guard"), description="", tags=[], spec=SPEC, created_by=None)
    if status != "draft":
        session.execute(text("UPDATE asset_versions_registry SET status='review' WHERE id=:id"), {"id": v.id})
    if status in ("published", "deprecated"):
        session.execute(text("UPDATE asset_versions_registry SET status='published', major=1, minor=0 WHERE id=:id"),
                        {"id": v.id})
    if status == "deprecated":
        session.execute(text("UPDATE asset_versions_registry SET status='deprecated' WHERE id=:id"), {"id": v.id})
    return c, v


def refused(session, sql, **params):
    sp = session.begin_nested()
    with pytest.raises(IntegrityError):
        session.execute(text(sql), params)
    sp.rollback()


def test_guard_draft_is_free(session):
    _, v = _version(session, "draft")
    session.execute(text("UPDATE character_versions SET spec_digest='x', height_m=2 WHERE id=:id"), {"id": v.id})
    refused(session, "UPDATE character_versions SET character_id=gen_random_uuid() WHERE id=:id", id=v.id)


def test_guard_review_freezes_the_build(session):
    _, v = _version(session, "review")
    refused(session, "UPDATE character_versions SET spec='{}'::jsonb WHERE id=:id", id=v.id)
    refused(session, "UPDATE character_versions SET validation_report='{\"ok\": true}'::jsonb WHERE id=:id", id=v.id)
    session.execute(text("UPDATE character_versions SET review_decision='approve', reviewed_at=now(), "
                         "manifest_key='m', published_at=now() WHERE id=:id"), {"id": v.id})


@pytest.mark.parametrize("status", ["published", "deprecated"])
def test_guard_published_is_immutable(session, status):
    c, v = _version(session, status)
    for sql in ("UPDATE character_versions SET spec='{}'::jsonb WHERE id=:id",
                "UPDATE character_versions SET review_notes='rewrite history' WHERE id=:id",
                "UPDATE character_versions SET manifest_sha256=repeat('0', 64) WHERE id=:id",
                "DELETE FROM character_versions WHERE id=:id",
                "UPDATE asset_versions_registry SET major=9 WHERE id=:id",
                "DELETE FROM asset_versions_registry WHERE id=:id"):
        refused(session, sql, id=v.id)
    session.execute(text("UPDATE character_versions SET deprecated_at=now() WHERE id=:id"), {"id": v.id})
    refused(session, "UPDATE characters SET code='CHAR-999' WHERE id=:id", id=c.id)


def test_guard_recommended_must_be_published(session):
    c, draft = _version(session, "draft")
    refused(session, "UPDATE characters SET recommended_version_id=:v WHERE id=:id", v=draft.id, id=c.id)
    other, published = _version(session, "published")
    refused(session, "UPDATE characters SET recommended_version_id=:v WHERE id=:id", v=published.id, id=c.id)
    session.execute(text("UPDATE characters SET recommended_version_id=:v WHERE id=:id"),
                    {"v": published.id, "id": other.id})
