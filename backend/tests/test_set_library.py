"""Prop and Environment Library (SRS 12, Phase 7): catalogs, validation, identity that never
changes (a prop's category, an environment's template), the measured-check gate on submit,
review, publish numbering and the database guards.

Builds are recorded with the same service call the worker makes after a real Blender job
(``workers/tests/test_environment_e2e.py`` runs those jobs for real)."""

import uuid

import pytest
from app.assets.sets import environments, props
from sqlalchemy import text
from studio_contracts.environment import get_environment_def, get_prop_def

SHA_KEY = {"props": "prop_blend_sha256", "environments": "environment_blend_sha256"}
NAMES = {"props": ("prop.blend", "prop.json", "prop_report.json"),
         "environments": ("environment.blend", "environment.json", "layout_report.json")}
SVC = {"props": props, "environments": environments}


def prop_spec(key="chair", **kw):
    d = get_prop_def(key)
    return {"item": {"id": d.id, "version": d.version}, **kw}


def env_spec(key="living_room", **kw):
    d = get_environment_def(key)
    return {"template": {"id": d.id, "version": d.version}, **kw}


def create(api, plural, spec, name=None):
    r = api.post(f"/api/v1/{plural}", json={"name": name or f"Thing {uuid.uuid4().hex[:6]}", "tags": ["home"],
                                             "spec": spec})
    assert r.status_code == 201, r.text
    return r.json()


def fake_build(db, plural, version_id, job_id, *, ok=True, blend_sha="d" * 64):
    blend, spec, report = NAMES[plural]
    files = {"blend": blend, "spec": spec, "report": report, "thumbnail": "thumbnail.png", "glb": "preview.glb"}
    uploaded = [{"role": role, "storage_key": f"jobs/{job_id}/attempt-1/out/{name}",
                 "sha256": blend_sha if role == "blend" else "e" * 64, "size_bytes": 10,
                 "mime_type": "application/octet-stream"} for role, name in files.items()]
    svc = SVC[plural]
    with db.begin() as s:
        v = s.get(svc.kind.model, uuid.UUID(version_id))
        rep = {"ok": ok, "failed": [] if ok else ["no_overlap:sofa_1+table_1"], SHA_KEY[plural]: blend_sha,
               "checks": []}
        return svc.attach_build(s, v, uuid.UUID(job_id), uploaded,
                                {"resolved_digest": v.spec_digest, "report": rep, "check": {"ok": ok}}, "5.2.3")


def built(api, db, plural, spec, ok=True):
    o = create(api, plural, spec)
    vid = o["versions"][0]["id"]
    r = api.post(f"/api/v1/{plural}/{o['id']}/drafts/{vid}/generate", json={"previews": {"resolution": 96}})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert job["type"] == f"generate_{plural[:-1]}"
    assert fake_build(db, plural, vid, job["id"], ok=ok)["attached"]
    return o, vid, job["id"]


def publish_now(db, plural, oid, vid, job_id, major=1, minor=0):
    svc = SVC[plural]
    with db.begin() as s:
        v = svc.get_version(s, uuid.UUID(oid), uuid.UUID(vid), lock=True)
        prefix = svc.library_prefix(v, major, minor)
        files = [{"role": f.role, "storage_key": prefix + f.storage_key.rsplit("/", 1)[-1], "sha256": f.sha256,
                  "size_bytes": f.size_bytes, "mime_type": f.mime_type} for f in v.entry.files]
        svc.complete_publish(s, v, major=major, minor=minor, files=files,
                             manifest={"storage_key": prefix + "manifest.json", "sha256": "f" * 64},
                             published_by=None, expected_build=v.built_digest, job_id=uuid.UUID(job_id))


def published(api_as, db, plural, spec):
    creator = api_as("creator")
    o, vid, job_id = built(creator, db, plural, spec)
    assert creator.post(f"/api/v1/{plural}/{o['id']}/drafts/{vid}/submit").status_code == 200
    assert api_as("reviewer").post(f"/api/v1/{plural}/{o['id']}/versions/{vid}/review",
                                   json={"decision": "approve"}).status_code == 200
    publish_now(db, plural, o["id"], vid, job_id)
    return o, vid


def test_catalogs_and_validation(api_as):
    api = api_as("reviewer")
    items = {i["key"]: i for i in api.get("/api/v1/prop-items").json()}
    assert {"table", "chair", "sofa", "bed", "books", "ball", "toy_car", "tree", "car", "apple"} <= set(items)
    templates = {t["key"]: t for t in api.get("/api/v1/environment-templates").json()}
    assert set(templates) == {"living_room", "bedroom", "classroom", "park", "street"}
    assert {p["key"] for p in api.get("/api/v1/lighting-presets").json()} >= {"day", "night", "evening"}
    ok = api.post("/api/v1/props/spec/validate", json={"name": "T", **prop_spec("table", params={"size": 1})}).json()
    assert ok["valid"] and ok["resolved"]["size"] == [1.44, 0.9, 0.864]
    env = api.post("/api/v1/environments/spec/validate", json={"name": "Home", **env_spec()}).json()
    assert env["valid"] and {m["name"] for m in env["layout"]["marks"]} == {"centre", "door"}
    bad = api.post("/api/v1/environments/spec/validate", json={
        "name": "Home", **env_spec(lighting="disco", dressing=[{"prop": {"id": "PROPD-TREE", "version": "0.1"}}])}).json()
    assert not bad["valid"] and {i["code"] for i in bad["issues"]} >= {"unknown_lighting", "wrong_setting"}


def test_create_and_identity_never_changes(api_as, db):
    creator = api_as("creator")
    p = create(creator, "props", prop_spec("sofa", material={"color": "teal"}))
    assert p["code"].startswith("PROP-") and p["category"] == "furniture"
    (v,) = p["versions"]
    assert v["size"] == [1.9, 0.85, 0.85] and v["solid"] and v["seat_z"] == 0.425 and v["top_z"] is None
    r = creator.patch(f"/api/v1/props/{p['id']}/drafts/{v['id']}", json={"spec": prop_spec("ball")})
    assert r.status_code == 422 and r.json()["code"] == "category_changed"
    e = create(creator, "environments", env_spec("park"))
    assert e["code"].startswith("ENV-") and e["template_key"] == "park" and e["kind"] == "outdoor"
    ev = creator.get(f"/api/v1/environments/{e['id']}/versions/{e['versions'][0]['id']}").json()
    assert ev["lighting"] == "day" and ev["layout"]["kind"] == "outdoor" and ev["template_ref"] == "ENVD-PARK@0.1"
    r = creator.patch(f"/api/v1/environments/{e['id']}/drafts/{ev['id']}", json={"spec": env_spec("street")})
    assert r.status_code == 422 and r.json()["code"] == "template_key_changed"
    with db() as s, pytest.raises(Exception, match="template_key never changes"):
        s.execute(text("UPDATE environments SET template_key = 'street' WHERE id = :i"), {"i": e["id"]})
    with db() as s, pytest.raises(Exception, match="category never changes"):
        s.execute(text("UPDATE props SET category = 'toy' WHERE id = :i"), {"i": p["id"]})


def test_a_failed_measured_check_blocks_submit_and_review_cycle(api_as, db):
    creator, reviewer = api_as("creator"), api_as("reviewer")
    e, vid, _ = built(creator, db, "environments", env_spec("bedroom"), ok=False)
    base = f"/api/v1/environments/{e['id']}"
    r = creator.post(f"{base}/drafts/{vid}/submit")
    assert r.status_code == 409 and "measured check failed (no_overlap:sofa_1+table_1)" in r.json()["detail"]
    job = creator.post(f"{base}/drafts/{vid}/generate").json()["job"]
    fake_build(db, "environments", vid, job["id"], ok=True)
    assert creator.post(f"{base}/drafts/{vid}/submit").json()["status"] == "review"
    with db() as s, pytest.raises(Exception, match="in review, its build cannot change"):
        s.execute(text("UPDATE environment_versions SET layout = '{}' WHERE id = :i"), {"i": vid})
    assert creator.post(f"{base}/versions/{vid}/review", json={"decision": "approve"}).status_code == 403
    r = reviewer.post(f"{base}/versions/{vid}/review", json={"decision": "reject", "notes": "too dark"})
    assert r.json()["status"] == "draft" and r.json()["review_notes"] == "too dark"


def test_gallery_filters(api_as, db):
    creator = api_as("creator")
    a = create(creator, "props", prop_spec("ball"), name="Red ball")
    b = create(creator, "props", prop_spec("table"), name="Kitchen table")
    api = api_as("reviewer")
    by = lambda plural, **q: {x["id"] for x in api.get(f"/api/v1/{plural}", params={"limit": 200, **q}).json()["items"]}
    assert a["id"] in by("props", category="toy") and b["id"] not in by("props", category="toy")
    assert b["id"] in by("props", q="kitchen") and a["id"] not in by("props", q="kitchen")
    e = create(creator, "environments", env_spec("classroom"), name="Room 4B")
    assert e["id"] in by("environments", category="classroom") and e["id"] not in by("environments", category="park")
    assert e["id"] in by("environments", status="draft") and e["id"] not in by("environments", status="published")


def test_published_versions_are_frozen_and_numbering(api_as, db):
    creator = api_as("creator")
    e, vid = published(api_as, db, "environments", env_spec("living_room"))
    base = f"/api/v1/environments/{e['id']}"
    detail = creator.get(base).json()
    assert detail["latest_published"] == "1.0" and detail["recommended_version"] == "1.0"
    v = creator.get(f"{base}/versions/{vid}").json()
    assert all(f["storage_key"].startswith(f"library/environments/{e['id']}/versions/1.0/") for f in v["files"])
    assert creator.patch(f"{base}/drafts/{vid}", json={"spec": env_spec()}).json()["code"] == "not_a_draft"
    with db() as s, pytest.raises(Exception, match="cannot be changed"):
        s.execute(text("UPDATE environment_versions SET lighting = 'night' WHERE id = :i"), {"i": vid})
    with db() as s, pytest.raises(Exception, match="cannot be deleted"):
        s.execute(text("DELETE FROM environment_versions WHERE id = :i"), {"i": vid})
    # new lighting keeps every mark: minor; a smaller room or a lost mark breaks scenes: major
    with db.begin() as s:
        env = environments.get_environment(s, uuid.UUID(e["id"]))
        relit = environments.new_draft(s, env, based_on_version_id=uuid.UUID(vid),
                                       spec=env_spec(lighting="evening"), created_by=None)
        assert environments.next_number(s, relit, "minor") == (1, 1, [])
        smaller = environments.new_draft(s, env, based_on_version_id=None, spec=env_spec(size=(5.0, 4.5, 2.6)),
                                         created_by=None)
        assert environments.next_number(s, smaller, "minor")[:2] == (2, 0)
        assert "size" in environments.next_number(s, smaller, "minor")[2]
        nomarks = environments.new_draft(s, env, based_on_version_id=None,
                                         spec=env_spec(marks=[{"name": "sofa_front", "at": (0.5, 0.6)}]),
                                         created_by=None)
        assert environments.next_number(s, nomarks, "minor") == (2, 0, ["marks"])
    p, pvid = published(api_as, db, "props", prop_spec("table"))
    with db.begin() as s:
        prop = props.get_prop(s, uuid.UUID(p["id"]))
        recolour = props.new_draft(s, prop, based_on_version_id=uuid.UUID(pvid),
                                   spec=prop_spec("table", material={"color": "red"}), created_by=None)
        assert props.next_number(s, recolour, "minor") == (1, 1, [])
        taller = props.new_draft(s, prop, based_on_version_id=None, spec=prop_spec("table", params={"height": 1}),
                                 created_by=None)
        assert props.next_number(s, taller, "minor") == (2, 0, ["size", "top height"])


def test_openapi_has_the_set_routes():
    from app.main import app

    doc = app.openapi()
    assert {"/api/v1/props", "/api/v1/environments", "/api/v1/prop-items", "/api/v1/environment-templates",
            "/api/v1/lighting-presets", "/api/v1/environments/{owner_id}/drafts/{draft_id}/generate",
            "/api/v1/props/{owner_id}/versions/{version_id}/publish"} <= set(doc["paths"])
    ids = [op["operationId"] for p in doc["paths"].values() for op in p.values()]
    assert len(ids) == len(set(ids)) and "environments_generate" in ids
