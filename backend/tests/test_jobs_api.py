import uuid

from app.assets import registry
from studio_contracts.ids import AssetKind


def submit(api, params=None, key=None, type_="blender_smoke"):
    headers = {"Idempotency-Key": key} if key else {}
    return api.post("/api/v1/jobs", json={"type": type_, "params": params or {}}, headers=headers)


def test_submit_validates_and_dispatches(api, dispatcher):
    r = submit(api, {"size_m": 2})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] == "queued" and job["queue"] == "blender"
    assert job["params"] == {"object_name": "smoke_cube", "size_m": 2.0}  # defaults filled, validated
    assert dispatcher.sent == [(job["id"], "blender", 0)]


def test_idempotency_key(api, dispatcher):
    key = f"test-{uuid.uuid4()}"
    first = submit(api, {"size_m": 1}, key)
    again = submit(api, {"size_m": 1}, key)
    assert first.status_code == 202 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert len(dispatcher.sent) == 1
    clash = submit(api, {"size_m": 3}, key)
    assert clash.status_code == 409 and clash.json()["code"] == "idempotency_conflict"


def test_bad_requests_are_problems(api):
    r = submit(api, {"size_m": -1})
    assert r.status_code == 422 and r.json()["code"] == "invalid_job_params"
    r = submit(api, {"surprise": True})
    assert r.status_code == 422
    r = submit(api, type_="rm_rf")
    assert r.status_code == 422 and r.json()["code"] == "unknown_job_type"
    assert api.get(f"/api/v1/jobs/{uuid.uuid4()}").json()["code"] == "job_not_found"


def test_cancel_and_retry_a_queued_job(api, dispatcher):
    job = submit(api).json()
    r = api.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert r.json()["status"] == "cancelled"
    assert api.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 409
    r = api.post(f"/api/v1/jobs/{job['id']}/retry")
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert dispatcher.sent[-1][0] == job["id"]
    kinds = [e["message"] for e in api.get(f"/api/v1/jobs/{job['id']}/events").json()]
    assert kinds == ["queued", "cancelled before start", "queued by user retry"]


def test_list_jobs_pages_newest_first(api):
    ids = [submit(api, {"object_name": "paging"}).json()["id"] for _ in range(3)]
    page = api.get("/api/v1/jobs", params={"limit": 2, "type": "blender_smoke"}).json()
    assert [j["id"] for j in page["items"]] == ids[::-1][:2]
    rest = api.get("/api/v1/jobs", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert rest["items"][0]["id"] == ids[0]


def test_asset_metadata_endpoints(api, db, storage_as, key_prefix, tmp_path):
    work = storage_as("worker", "work")
    path = tmp_path / "f.blend"
    path.write_bytes(b"BLENDER-fake")
    info = work.put_file(f"{key_prefix}/f.blend", path, "application/x-blender")
    with db.begin() as s:
        v = registry.create_draft(s, AssetKind.test_asset)
        f = registry.add_file(s, v, role="blend", storage_key=info.key, sha256=info.sha256, size_bytes=12,
                              mime_type="application/x-blender")
        vid, fid, code = v.id, f.id, v.display_code
    assert code.startswith("TEST-")
    version = api.get(f"/api/v1/assets/versions/{vid}").json()
    assert version["status"] == "draft" and version["files"][0]["sha256"] == info.sha256
    meta = api.get(f"/api/v1/assets/files/{fid}").json()
    assert meta["storage_key"] == info.key and meta["size_bytes"] == 12
    import urllib.request

    url = api.get(f"/api/v1/assets/files/{fid}/download").json()["url"]
    assert urllib.request.urlopen(url).read() == b"BLENDER-fake"


def test_display_codes_are_sequential_and_never_reused(db):
    with db.begin() as s:
        a = registry.allocate_code(s, AssetKind.character)
        b = registry.allocate_code(s, AssetKind.character)
    n = int(a.split("-")[1])
    assert b == f"CHAR-{n + 1:03d}"
