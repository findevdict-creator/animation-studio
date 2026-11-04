"""The publisher: approved review -> immutable library version (SRS Phase 3, architecture 6.3).

Blender's part (re-validating character.blend) is replaced by ``fake_validator.py`` here;
``test_character_library_e2e.py`` runs the whole thing with real Blender builds."""

import json
import sys
import uuid
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from app.assets import characters as svc
from app.config import get_settings
from app.core.storage import ObjectExists, StorageError, sha256_file
from app.models.audit import AuditLog
from app.models.characters import Character, CharacterVersion
from app.models.jobs import Job
from studio_contracts.jobs import JobStatus
from studio_workers.publisher import PublisherRunner

FAKE = [sys.executable, str(Path(__file__).with_name("fake_validator.py"))]
SPEC = {"body": {"age_appearance": "child", "height_m": 1.28}, "hair": {"style": "short"}}
FILES = (("blend", "character.blend", "application/x-blender"), ("spec", "character.json", "application/json"),
         ("report", "validation.json", "application/json"), ("thumbnail", "thumbnail.png", "image/png"),
         ("glb", "preview.glb", "model/gltf-binary"))


@pytest.fixture
def runner(storage_as, dispatcher, tmp_path):
    settings = get_settings().model_copy(update={"work_root": str(tmp_path / "work")})

    def make():
        return PublisherRunner(settings, work_storage=storage_as("publisher", "work"),
                               library_storage=storage_as("publisher", "library"), dispatcher=dispatcher,
                               command=FAKE)
    return make


def build(api, db, storage_as, tmp_path, cid, vid, mode="ok"):
    """A finished generate job for the draft, with real objects in the work bucket."""
    job = api.post(f"/api/v1/characters/{cid}/drafts/{vid}/generate").json()["job"]
    with db() as s:
        digest = s.get(CharacterVersion, uuid.UUID(vid)).spec_digest
    work = storage_as("worker", "work")
    uploaded = []
    for role, name, mime in FILES:
        path = tmp_path / f"{job['id']}-{name}"
        path.write_text(json.dumps({"digest": digest, "mode": mode}) if role == "blend" else f"{name} {vid}")
        key = f"jobs/{job['id']}/attempt-1/out/{name}"
        work.put_file(key, path, mime, sha256=sha256_file(path))
        uploaded.append({"role": role, "storage_key": key, "sha256": sha256_file(path),
                         "size_bytes": path.stat().st_size, "mime_type": mime})
    blend_sha = uploaded[0]["sha256"]
    with db.begin() as s:
        v = s.get(CharacterVersion, uuid.UUID(vid))
        svc.attach_build(s, v, uuid.UUID(job["id"]), uploaded,
                         {"resolved_digest": digest, "height_m": 1.28,
                          "report": {"ok": True, "blend_sha256": blend_sha, "checks": [{"name": "x", "ok": True}],
                                     "engine_version": "0.3.0"}}, "5.2.3")
    return uploaded


def approved(api_as, db, storage_as, tmp_path, cid=None, based_on=None, spec=None, mode="ok"):
    creator, reviewer = api_as("creator"), api_as("reviewer")
    if cid is None:
        c = creator.post("/api/v1/characters", json={"name": f"Pub {uuid.uuid4().hex[:6]}", "spec": spec or SPEC}).json()
        cid, vid = c["id"], c["versions"][0]["id"]
    else:
        body = {"based_on_version_id": based_on} if spec is None else {"based_on_version_id": based_on, "spec": spec}
        r = creator.post(f"/api/v1/characters/{cid}/drafts", json=body)
        assert r.status_code == 201, r.text
        vid = r.json()["id"]
    build(creator, db, storage_as, tmp_path, cid, vid, mode)
    assert creator.post(f"/api/v1/characters/{cid}/drafts/{vid}/submit").status_code == 200
    r = reviewer.post(f"/api/v1/characters/{cid}/versions/{vid}/review", json={"decision": "approve"})
    assert r.status_code == 200
    return cid, vid


def publish(api_as, runner, cid, vid, bump="minor"):
    r = api_as("director").post(f"/api/v1/characters/{cid}/versions/{vid}/publish", json={"bump": bump})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert job["queue"] == "publish" and job["type"] == "publish_character"
    outcome = runner().run(uuid.UUID(job["id"]))
    return outcome, job["id"]


def test_publish_first_version(api_as, db, storage_as, runner, tmp_path):
    cid, vid = approved(api_as, db, storage_as, tmp_path)
    outcome, job_id = publish(api_as, runner, cid, vid)
    with db() as s:
        job = s.get(Job, uuid.UUID(job_id))
        assert outcome.status == JobStatus.succeeded, job.error
        v = s.get(CharacterVersion, uuid.UUID(vid))
        assert v.status == "published" and v.version_label == "1.0" and v.published_by == job.requested_by
        assert s.get(Character, uuid.UUID(cid)).recommended_version_id == v.id
        prefix = f"library/characters/{cid}/versions/1.0/"
        files = {f.storage_key: f for f in v.entry.files}
        assert sorted(k.removeprefix(prefix) for k in files) == sorted(
            [n for _, n, _ in FILES] + ["manifest.json", "publish_validation.json"])
        assert v.manifest_key == prefix + "manifest.json"
        assert s.query(AuditLog).filter_by(action="character.published", target_id=v.id).count() == 1

    library = storage_as("api", "library")  # every identity can read the library
    manifest = json.loads(library.get_bytes(prefix + "manifest.json"))
    assert manifest["version"] == "1.0" and manifest["version_id"] == vid and manifest["kind"] == "character"
    assert manifest["validation"]["publish"]["ok"] and manifest["body_fit_profile"] == "child_average"
    assert len(manifest["shape_keys"]["visemes"]) == 10
    for entry in manifest["files"]:  # SHA-256 of every library object matches the manifest and the rows
        local = tmp_path / ("dl-" + entry["key"])
        library.get_file(prefix + entry["key"], local, expected_sha256=entry["sha256"])
        assert files[prefix + entry["key"]].sha256 == entry["sha256"] == sha256_file(local)
    assert sha256_file_bytes(library.get_bytes(prefix + "manifest.json")) == v.manifest_sha256

    # replaying the same publish (a duplicate delivery or an Idempotency-Key replay) changes nothing
    with db.begin() as s:
        s.get(Job, uuid.UUID(job_id)).status = "running"
    runner().run(uuid.UUID(job_id))
    with db() as s:
        assert s.get(Job, uuid.UUID(job_id)).result.get("version") == "1.0"


def sha256_file_bytes(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


def test_published_version_cannot_be_overwritten(api_as, db, storage_as, runner, tmp_path):
    cid, vid = approved(api_as, db, storage_as, tmp_path)
    assert publish(api_as, runner, cid, vid)[0].status == JobStatus.succeeded
    base, creator, director = f"/api/v1/characters/{cid}", api_as("creator"), api_as("director")
    key = f"library/characters/{cid}/versions/1.0/character.blend"
    junk = tmp_path / "junk.blend"
    junk.write_bytes(b"not the character")

    # API: no route changes a published version
    assert creator.patch(f"{base}/drafts/{vid}", json={"spec": SPEC}).json()["code"] == "not_a_draft"
    assert creator.post(f"{base}/drafts/{vid}/generate").json()["code"] == "not_a_draft"
    assert creator.delete(f"{base}/drafts/{vid}").json()["code"] == "not_a_draft"
    assert director.post(f"{base}/versions/{vid}/publish").json()["code"] == "invalid_version_status"
    assert api_as("reviewer").post(f"{base}/versions/{vid}/review",
                                   json={"decision": "reject"}).json()["code"] == "invalid_version_status"
    # storage: the worker and API identities cannot write the library at all
    for identity in ("worker", "api"):
        with pytest.raises((ClientError, StorageError)):
            storage_as(identity, "library").put_file(key, junk, "application/x-blender")
    # and the publisher itself cannot replace an object (write-once)
    with pytest.raises(ObjectExists):
        storage_as("publisher", "library").put_immutable(key, junk, "application/x-blender", sha256_file(junk))
    # database: the triggers refuse changes to the version and its files
    with db() as s, pytest.raises(Exception, match="cannot be changed|cannot be update"):
        v = s.get(CharacterVersion, uuid.UUID(vid))
        v.spec = {"tampered": True}
        s.commit()
    with db() as s:
        v = s.get(CharacterVersion, uuid.UUID(vid))
        assert v.spec.get("tampered") is None and v.version_label == "1.0"
    with db() as s, pytest.raises(Exception, match="cannot be update"):
        f = next(f for f in s.get(CharacterVersion, uuid.UUID(vid)).entry.files if f.role == "blend")
        f.sha256 = "0" * 64
        s.commit()


def test_new_draft_from_published_and_version_numbers(api_as, db, storage_as, runner, tmp_path):
    cid, v1 = approved(api_as, db, storage_as, tmp_path)
    assert publish(api_as, runner, cid, v1)[0].status == JobStatus.succeeded
    _, v11 = approved(api_as, db, storage_as, tmp_path, cid=cid, based_on=v1)
    assert publish(api_as, runner, cid, v11)[0].status == JobStatus.succeeded
    _, v2 = approved(api_as, db, storage_as, tmp_path, cid=cid, based_on=v11)
    assert publish(api_as, runner, cid, v2, bump="major")[0].status == JobStatus.succeeded
    # a different body-fit profile breaks outfit compatibility: major even when minor was asked
    adult = {**SPEC, "body": {"age_appearance": "adult", "height_m": 1.7}}
    _, v3 = approved(api_as, db, storage_as, tmp_path, cid=cid, based_on=v2, spec=adult)
    assert publish(api_as, runner, cid, v3, bump="minor")[0].status == JobStatus.succeeded
    history = api_as("reviewer").get(f"/api/v1/characters/{cid}/versions").json()
    assert [(h["id"], h["version_label"], h["status"]) for h in history] == [
        (v3, "3.0", "published"), (v2, "2.0", "published"), (v11, "1.1", "published"), (v1, "1.0", "published")]
    assert [h["based_on_version_id"] for h in history] == [v2, v11, v1, None]
    detail = api_as("reviewer").get(f"/api/v1/characters/{cid}").json()
    assert detail["latest_published"] == "3.0" and detail["recommended_version"] == "1.0"  # recommended never moves by itself
    # each version's files are separate objects, loadable independently
    lib = storage_as("api", "library")
    for label in ("1.0", "1.1", "2.0", "3.0"):
        m = json.loads(lib.get_bytes(f"library/characters/{cid}/versions/{label}/manifest.json"))
        assert m["version"] == label
    r = api_as("director").patch(f"/api/v1/characters/{cid}", json={"recommended_version_id": v2})
    assert r.status_code == 200 and r.json()["recommended_version"] == "2.0"
    r = api_as("director").post(f"/api/v1/characters/{cid}/versions/{v1}/deprecate")
    assert r.status_code == 200 and r.json()["status"] == "deprecated" and r.json()["deprecated_at"]
    r = api_as("reviewer").get(f"/api/v1/characters/{cid}/versions/{v1}/files/character.blend", follow_redirects=False)
    assert r.status_code == 302 and "/studio-library/" in r.headers["location"]  # deprecated stays loadable


def test_publish_refuses_unapproved_and_tampered(api_as, db, storage_as, runner, tmp_path):
    c = api_as("creator").post("/api/v1/characters", json={"name": f"Pub {uuid.uuid4().hex[:6]}", "spec": SPEC}).json()
    cid, vid = c["id"], c["versions"][0]["id"]
    uploaded = build(api_as("creator"), db, storage_as, tmp_path, cid, vid)
    assert api_as("director").post(f"/api/v1/characters/{cid}/versions/{vid}/publish").json()["code"] == "invalid_version_status"
    api_as("creator").post(f"/api/v1/characters/{cid}/drafts/{vid}/submit")
    r = api_as("director").post(f"/api/v1/characters/{cid}/versions/{vid}/publish")
    assert r.status_code == 409 and r.json()["code"] == "not_approved"
    assert api_as("creator").post(f"/api/v1/characters/{cid}/versions/{vid}/publish").status_code == 403
    api_as("reviewer").post(f"/api/v1/characters/{cid}/versions/{vid}/review", json={"decision": "approve"})

    # someone with work-bucket access swaps the draft's thumbnail after approval
    swapped = tmp_path / "swapped.png"
    swapped.write_bytes(b"different picture")
    thumb = next(u for u in uploaded if u["role"] == "thumbnail")
    storage_as("worker", "work").put_file(thumb["storage_key"], swapped, "image/png")
    outcome, job_id = publish(api_as, runner, cid, vid)
    with db() as s:
        job = s.get(Job, uuid.UUID(job_id))
        assert outcome.status == JobStatus.failed and job.error["kind"] == "refused"
        assert "thumbnail.png" in job.error["message"] and "SHA-256" in job.error["message"]
        v = s.get(CharacterVersion, uuid.UUID(vid))
        assert v.status == "review" and v.version_label is None and v.manifest_key is None


def test_failed_revalidation_leaves_the_version_in_review(api_as, db, storage_as, runner, tmp_path):
    cid, vid = approved(api_as, db, storage_as, tmp_path, mode="invalid")
    outcome, job_id = publish(api_as, runner, cid, vid)
    with db() as s:
        job = s.get(Job, uuid.UUID(job_id))
        assert outcome.status == JobStatus.failed and "re-validation" in job.error["message"]
        assert s.get(CharacterVersion, uuid.UUID(vid)).status == "review"


def test_interrupted_publish_resumes_with_the_same_number(api_as, db, storage_as, runner, tmp_path, monkeypatch):
    cid, vid = approved(api_as, db, storage_as, tmp_path)
    real = svc.complete_publish
    calls = []

    def crash_once(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("database went away")
        return real(*a, **kw)

    monkeypatch.setattr(svc, "complete_publish", crash_once)
    outcome, job_id = publish(api_as, runner, cid, vid)
    assert outcome.status == JobStatus.queued and outcome.retried  # every library object is written by now
    with db() as s:
        assert s.get(CharacterVersion, uuid.UUID(vid)).status == "review"
    second = runner().run(uuid.UUID(job_id))
    with db() as s:
        assert second.status == JobStatus.succeeded, s.get(Job, uuid.UUID(job_id)).error
        assert s.get(CharacterVersion, uuid.UUID(vid)).version_label == "1.0"


def test_a_number_with_objects_from_another_version_is_skipped(api_as, db, storage_as, runner, tmp_path):
    cid, vid = approved(api_as, db, storage_as, tmp_path)
    marker = tmp_path / "claim.json"
    marker.write_text(json.dumps({"version_id": str(uuid.uuid4())}))
    storage_as("publisher", "library").put_immutable(f"library/characters/{cid}/versions/1.0/version.claim.json",
                                                     marker, "application/json", sha256_file(marker))
    assert publish(api_as, runner, cid, vid)[0].status == JobStatus.succeeded
    with db() as s:
        assert s.get(CharacterVersion, uuid.UUID(vid)).version_label == "1.1"
