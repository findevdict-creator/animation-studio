"""Accessory Library: the catalog, validation, identity and category, drafts, the attach gate
on submit, review, publish numbering and the database guards (SRS 8, Phase 5).

Builds are recorded with the same service call the worker makes after a real Blender job
(``workers/tests/test_scene_e2e.py`` runs those jobs for real)."""

import uuid

import pytest
from app.assets import accessories as svc
from app.models.accessories import AccessoryVersion
from sqlalchemy import text
from studio_contracts.accessory import get_accessory_def

ROLES = {"blend": "accessory.blend", "spec": "accessory.json", "report": "attach_report.json",
         "thumbnail": "thumbnail.png", "preview": "on_reference.png", "glb": "preview.glb"}


def acc_spec(key="glasses", **kw):
    d = get_accessory_def(key)
    return {"item": {"id": d.id, "version": d.version}, **kw}


def create(api, spec=None, name=None):
    r = api.post("/api/v1/accessories", json={"name": name or f"Specs {uuid.uuid4().hex[:6]}", "tags": ["eyewear"],
                                               "spec": spec or acc_spec()})
    assert r.status_code == 201, r.text
    return r.json()


def fake_build(db, version_id, job_id, *, ok=True, blend_sha="d" * 64):
    uploaded = [{"role": role, "storage_key": f"jobs/{job_id}/attempt-1/out/{name}",
                 "sha256": blend_sha if role == "blend" else "e" * 64, "size_bytes": 10,
                 "mime_type": "application/octet-stream"} for role, name in ROLES.items()]
    with db.begin() as s:
        v = s.get(AccessoryVersion, uuid.UUID(version_id))
        report = {"ok": ok, "failed": [] if ok else ["clear_of_body:ACC_glasses"], "accessory_blend_sha256": blend_sha,
                  "checks": []}
        return svc.attach_build(s, v, uuid.UUID(job_id), uploaded,
                                {"resolved_digest": v.spec_digest, "report": report, "check": {"ok": ok}}, "5.2.3")


def generate(api, aid, vid):
    r = api.post(f"/api/v1/accessories/{aid}/drafts/{vid}/generate", json={"previews": {"resolution": 96}})
    assert r.status_code == 202, r.text
    return r.json()


def built(api, db, spec=None, ok=True):
    a = create(api, spec)
    vid = a["versions"][0]["id"]
    job = generate(api, a["id"], vid)["job"]
    assert fake_build(db, vid, job["id"], ok=ok)["attached"]
    return a, vid, job["id"]


def publish_now(db, aid, vid, job_id, major=1, minor=0):
    """The publisher's final transaction, with made-up library files."""
    with db.begin() as s:
        v = svc.get_version(s, uuid.UUID(aid), uuid.UUID(vid), lock=True)
        prefix = svc.library_prefix(v, major, minor)
        files = [{"role": f.role, "storage_key": prefix + f.storage_key.rsplit("/", 1)[-1], "sha256": f.sha256,
                  "size_bytes": f.size_bytes, "mime_type": f.mime_type} for f in v.entry.files]
        svc.complete_publish(s, v, major=major, minor=minor, files=files,
                             manifest={"storage_key": prefix + "manifest.json", "sha256": "f" * 64},
                             published_by=None, expected_build=v.built_digest, job_id=uuid.UUID(job_id))


def published(api_as, db, spec=None):
    creator = api_as("creator")
    a, vid, job_id = built(creator, db, spec)
    assert creator.post(f"/api/v1/accessories/{a['id']}/drafts/{vid}/submit").status_code == 200
    assert api_as("reviewer").post(f"/api/v1/accessories/{a['id']}/versions/{vid}/review",
                                   json={"decision": "approve"}).status_code == 200
    publish_now(db, a["id"], vid, job_id)
    return a, vid


def test_catalog_and_validation(api_as):
    api = api_as("reviewer")
    items = {i["key"]: i for i in api.get("/api/v1/accessory-items").json()}
    assert set(items) == {"glasses", "beanie", "watch", "tote", "teddy"}
    assert items["watch"]["attach_points"] == ["wrist_L", "wrist_R"]
    assert items["beanie"]["conflicts_with_slots"] == ["headwear"]
    conv = api.get("/api/v1/style-profiles/family_cartoon@1.0/accessories").json()
    assert "plastic" in conv["finishes"] and conv["poses"][0] == "rest"
    ok = api.post("/api/v1/accessories/spec/validate", json={"name": "W", **acc_spec("watch", attach_point="wrist_R")})
    assert ok.json()["valid"] and ok.json()["resolved"]["attach_bone"] == "DEF-forearm.R.001"
    bad = api.post("/api/v1/accessories/spec/validate", json={
        "name": "W", **acc_spec("watch", attach_point="head_top", params={"face_size": 3, "colour": 1},
                                material={"finish": "plush", "color": "mauve"}, fit_profiles=["giant"])}).json()
    assert not bad["valid"]
    assert {i["code"] for i in bad["issues"]} >= {"not_for_item", "out_of_range", "unknown_param", "unknown_color",
                                                   "unknown_fit_profile"}


def test_create_and_the_category_never_changes(api_as, db):
    creator = api_as("creator")
    a = create(creator, acc_spec("watch", fit_profiles=["child_average", "child_slim"]))
    assert a["code"].startswith("ACC-") and a["category"] == "wristwear"
    (v,) = a["versions"]
    assert v["attach_point"] == "wrist_L" and v["attach_points"] == ["wrist_L", "wrist_R"]
    assert v["fit_profiles"] == ["child_average", "child_slim"]
    detail = creator.get(f"/api/v1/accessories/{a['id']}/versions/{v['id']}").json()
    assert detail["ready_for_review"] == ["no build yet: run generate",
                                          "missing files: blend, spec, report, thumbnail"]
    r = creator.patch(f"/api/v1/accessories/{a['id']}/drafts/{v['id']}", json={"spec": acc_spec("beanie")})
    assert r.status_code == 422 and r.json()["code"] == "category_changed"
    with db() as s, pytest.raises(Exception, match="category never changes"):
        s.execute(text("UPDATE accessories SET category = 'toy' WHERE id = :i"), {"i": a["id"]})
    bad = creator.post("/api/v1/accessories", json={"name": "x", "spec": acc_spec("teddy", attach_point="wrist_L")})
    assert bad.status_code == 422 and bad.json()["issues"][0]["code"] == "not_for_item"


def test_a_failed_attach_check_blocks_submit_and_review_cycle(api_as, db):
    creator, reviewer = api_as("creator"), api_as("reviewer")
    a, vid, _ = built(creator, db, ok=False)
    base = f"/api/v1/accessories/{a['id']}"
    r = creator.post(f"{base}/drafts/{vid}/submit")
    assert r.status_code == 409 and "attach check on the reference body failed" in r.json()["detail"]
    job = generate(creator, a["id"], vid)["job"]
    fake_build(db, vid, job["id"], ok=True)
    assert creator.post(f"{base}/drafts/{vid}/submit").json()["status"] == "review"
    with db() as s, pytest.raises(Exception, match="in review, its build cannot change"):
        s.execute(text("UPDATE accessory_versions SET check_report = '{}' WHERE id = :i"), {"i": vid})
    assert creator.post(f"{base}/versions/{vid}/review", json={"decision": "approve"}).status_code == 403
    r = reviewer.post(f"{base}/versions/{vid}/review", json={"decision": "reject", "notes": "lenses too big"})
    assert r.json()["status"] == "draft" and r.json()["review_notes"] == "lenses too big"
    assert api_as("director").post(f"{base}/versions/{vid}/publish").json()["code"] in ("invalid_version_status",)


def test_gallery_filters(api_as, db):
    creator = api_as("creator")
    w = create(creator, acc_spec("watch", fit_profiles=["child_slim"]), name="Blue watch")
    g = create(creator, acc_spec("glasses"), name="Round glasses")
    api = api_as("reviewer")
    by = lambda **q: {x["id"] for x in api.get("/api/v1/accessories", params={"limit": 200, **q}).json()["items"]}
    assert w["id"] in by(category="wristwear") and g["id"] not in by(category="wristwear")
    assert w["id"] in by(attach_point="wrist_R") and g["id"] not in by(attach_point="wrist_R")
    # an accessory declared for no profile in particular goes on every profile
    assert {w["id"], g["id"]} <= by(fit_profile="child_slim")
    assert g["id"] in by(fit_profile="adult_stocky") and w["id"] not in by(fit_profile="adult_stocky")
    assert w["id"] in by(q="blue")


def test_published_versions_are_frozen_and_numbering(api_as, db):
    creator = api_as("creator")
    a, vid = published(api_as, db, acc_spec("watch"))
    base = f"/api/v1/accessories/{a['id']}"
    detail = creator.get(base).json()
    assert detail["latest_published"] == "1.0" and detail["recommended_version"] == "1.0"
    v = creator.get(f"{base}/versions/{vid}").json()
    assert {f["name"] for f in v["files"]} >= {"accessory.blend", "attach_report.json"}
    assert all(f["storage_key"].startswith(f"library/accessories/{a['id']}/versions/1.0/") for f in v["files"])
    assert creator.patch(f"{base}/drafts/{vid}", json={"spec": acc_spec("watch")}).json()["code"] == "not_a_draft"
    with db() as s, pytest.raises(Exception, match="cannot be changed"):
        s.execute(text("UPDATE accessory_versions SET spec = '{}' WHERE id = :i"), {"i": vid})
    with db() as s, pytest.raises(Exception, match="cannot be deleted"):
        s.execute(text("DELETE FROM accessory_versions WHERE id = :i"), {"i": vid})
    # a wider draft is a minor bump; dropping an attachment point or narrowing profiles is major
    with db.begin() as s:
        acc = svc.get_accessory(s, uuid.UUID(a["id"]))
        same = svc.new_draft(s, acc, based_on_version_id=uuid.UUID(vid), spec=acc_spec("watch", params={"face_size": 0.8}),
                             created_by=None)
        assert svc.next_number(s, same, "minor") == (1, 1, [])
        narrow = svc.new_draft(s, acc, based_on_version_id=None, spec=acc_spec("watch", fit_profiles=["child_slim"]),
                               created_by=None)
        assert svc.next_number(s, narrow, "minor") == (2, 0, ["body-fit profiles"])


def test_openapi_has_the_accessory_routes():
    from app.main import app

    paths = app.openapi()["paths"]
    assert {"/api/v1/accessories", "/api/v1/accessory-items", "/api/v1/accessories/{accessory_id}/drafts/{draft_id}/generate",
            "/api/v1/accessories/{accessory_id}/versions/{version_id}/publish"} <= set(paths)
    assert not [n for n in app.openapi()["components"]["schemas"] if "__" in n]
