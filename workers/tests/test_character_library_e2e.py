"""SRS Phase 3 acceptance as a test, with real Blender: publish 1.0, refuse to overwrite it,
draft from it, publish 2.0, then link both library files into one scene and validate each."""

import json
import uuid

import pytest

from app.config import get_settings
from app.core.storage import ObjectExists, sha256_file
from app.models.jobs import Job
from studio_contracts.jobs import JobStatus
from studio_workers.publisher import PublisherRunner
from studio_workers.supervisor import Supervisor

pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")
SMALL = {"resolution": 96, "samples": 2, "turntable_frames": 4, "sheet_tile": 64}
SPEC = {"body": {"age_appearance": "child", "height_m": 1.28}, "hair": {"style": "short", "color": "black"}}


def test_publish_two_versions_and_load_both(api_as, db, storage_as, dispatcher, tmp_path):
    settings = get_settings().model_copy(update={"work_root": str(tmp_path / "work"), "blender_mode": "wheel"})
    creator, reviewer, director = api_as("creator"), api_as("reviewer"), api_as("director")

    def build_and_approve(cid, vid):
        job = creator.post(f"/api/v1/characters/{cid}/drafts/{vid}/generate", json={"previews": SMALL}).json()["job"]
        out = Supervisor(settings, work_storage=storage_as("worker", "work"), dispatcher=dispatcher).run(
            uuid.UUID(job["id"]))
        with db() as s:
            assert out.status == JobStatus.succeeded, s.get(Job, uuid.UUID(job["id"])).error
        assert creator.post(f"/api/v1/characters/{cid}/drafts/{vid}/submit").status_code == 200
        assert reviewer.post(f"/api/v1/characters/{cid}/versions/{vid}/review",
                             json={"decision": "approve"}).status_code == 200

    def publish(cid, vid, bump):
        job = director.post(f"/api/v1/characters/{cid}/versions/{vid}/publish", json={"bump": bump}).json()["job"]
        out = PublisherRunner(settings, work_storage=storage_as("publisher", "work"),
                              library_storage=storage_as("publisher", "library"), dispatcher=dispatcher
                              ).run(uuid.UUID(job["id"]))
        with db() as s:
            j = s.get(Job, uuid.UUID(job["id"]))
            assert out.status == JobStatus.succeeded, j.error
            return j.result

    c = creator.post("/api/v1/characters", json={"name": f"Ali {uuid.uuid4().hex[:6]}", "spec": SPEC}).json()
    cid, v1 = c["id"], c["versions"][0]["id"]
    build_and_approve(cid, v1)
    r1 = publish(cid, v1, "minor")
    assert r1["version"] == "1.0"

    blend_key = f"library/characters/{cid}/versions/1.0/character.blend"
    other = tmp_path / "other.blend"
    other.write_bytes(b"x")
    with pytest.raises(ObjectExists):
        storage_as("publisher", "library").put_immutable(blend_key, other, "application/x-blender", sha256_file(other))

    d = creator.post(f"/api/v1/characters/{cid}/drafts", json={"based_on_version_id": v1}).json()
    spiky = {**d["spec"], "hair": {"style": "spiky", "color": "brown"}, "body": {**d["spec"]["body"], "height_m": 1.3}}
    v2 = creator.patch(f"/api/v1/characters/{cid}/drafts/{d['id']}", json={"spec": spiky}).json()["id"]
    build_and_approve(cid, v2)
    r2 = publish(cid, v2, "major")
    assert r2["version"] == "2.0"

    lib = storage_as("api", "library")
    paths = []
    for label in ("1.0", "2.0"):
        prefix = f"library/characters/{cid}/versions/{label}/"
        manifest = json.loads(lib.get_bytes(prefix + "manifest.json"))
        blend = next(f for f in manifest["files"] if f["key"] == "character.blend")
        dest = tmp_path / label / "character.blend"
        dest.parent.mkdir()
        lib.get_file(prefix + "character.blend", dest, expected_sha256=blend["sha256"])
        paths.append(dest)
        assert manifest["validation"]["publish"]["ok"] and manifest["validation"]["publish"]["checks"] == 22

    from cartoon_engine.tools.load_check import check

    doc = check(paths)
    assert doc["ok"], doc
    a, b = doc["characters"]
    assert a["stamps"]["studio_height_m"] == 1.28 and b["stamps"]["studio_height_m"] == 1.3
    assert a["stamps"]["studio_spec_digest"] != b["stamps"]["studio_spec_digest"]
