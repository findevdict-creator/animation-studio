"""The worker stages a fit job's inputs from the exact files of both versions (library for
published, work bucket for drafts), checked against their rows; Blender is a stand-in."""

import sys
import uuid
from pathlib import Path

from app.assets import characters as char_svc
from app.assets import outfits as outfit_svc
from app.config import get_settings
from app.core.storage import sha256_file
from app.models.characters import CharacterVersion
from app.models.jobs import Job
from app.models.outfits import OutfitVersion
from studio_contracts.jobs import JobStatus
from studio_contracts.outfit import get_garment
from studio_workers.supervisor import Supervisor

FAKE = [sys.executable, str(Path(__file__).with_name("fake_fitter.py"))]
CHAR = {"body": {"age_appearance": "child", "height_m": 1.28}}


def piece(key):
    g = get_garment(key)
    return {"slot": g.slot, "garment": {"id": g.id, "version": g.version}}


def _put(store, key, path, text, mime="application/octet-stream"):
    path.write_text(text)
    store.put_file(key, path, mime, sha256=sha256_file(path))
    return {"storage_key": key, "sha256": sha256_file(path), "size_bytes": path.stat().st_size, "mime_type": mime}


def setup(api_as, db, storage_as, tmp_path):
    creator, reviewer = api_as("creator"), api_as("reviewer")
    c = creator.post("/api/v1/characters", json={"name": f"In {uuid.uuid4().hex[:6]}", "spec": CHAR}).json()
    cid, cvid = c["id"], c["versions"][0]["id"]
    job = creator.post(f"/api/v1/characters/{cid}/drafts/{cvid}/generate").json()["job"]
    work, library = storage_as("worker", "work"), storage_as("publisher", "library")
    files = [{"role": r, **_put(work, f"jobs/{job['id']}/out/{n}", tmp_path / n, f"{n} {cvid}")}
             for r, n in (("blend", "character.blend"), ("spec", "character.json"), ("report", "validation.json"),
                          ("thumbnail", "thumbnail.png"), ("glb", "preview.glb"))]
    with db.begin() as s:
        v = s.get(CharacterVersion, uuid.UUID(cvid))
        char_svc.attach_build(s, v, uuid.UUID(job["id"]), files, {"resolved_digest": v.spec_digest, "report": {
            "ok": True, "blend_sha256": files[0]["sha256"]}}, "5.2.3")
    creator.post(f"/api/v1/characters/{cid}/drafts/{cvid}/submit")
    reviewer.post(f"/api/v1/characters/{cid}/versions/{cvid}/review", json={"decision": "approve"})
    prefix = f"library/characters/{cid}/versions/1.0/"
    lib_files = [{"role": f["role"], **_put(library, prefix + f["storage_key"].rsplit("/", 1)[-1],
                                            tmp_path / ("lib-" + f["storage_key"].rsplit("/", 1)[-1]),
                                            f"{f['storage_key']} {cvid}")} for f in files]
    with db.begin() as s:
        v = char_svc.get_version(s, uuid.UUID(cid), uuid.UUID(cvid), lock=True)
        char_svc.complete_publish(s, v, major=1, minor=0, files=lib_files,
                                  manifest={"storage_key": prefix + "manifest.json", "sha256": "f" * 64},
                                  published_by=None, expected_build=v.built_digest, job_id=uuid.UUID(job["id"]))

    spec = {"fit_profiles": ["child_average"], "pieces": [piece("tshirt"), piece("shorts")]}
    o = creator.post("/api/v1/outfits", json={"name": f"Fit {uuid.uuid4().hex[:6]}", "spec": spec}).json()
    oid, ovid = o["id"], o["versions"][0]["id"]
    job = creator.post(f"/api/v1/outfits/{oid}/drafts/{ovid}/generate").json()["job"]
    ofiles = [{"role": r, **_put(work, f"jobs/{job['id']}/out/{n}", tmp_path / n, f"{n} {ovid}")}
              for r, n in (("blend", "outfit.blend"), ("spec", "outfit.json"), ("report", "fit_report.json"),
                           ("thumbnail", "thumbnail.png"), ("glb", "preview.glb"))]
    with db.begin() as s:
        v = s.get(OutfitVersion, uuid.UUID(ovid))
        outfit_svc.attach_build(s, v, uuid.UUID(job["id"]), ofiles, {"resolved_digest": v.spec_digest, "report": {
            "ok": True, "outfit_blend_sha256": ofiles[0]["sha256"]}}, "5.2.3")
    return oid, ovid, cvid, ofiles


def test_fit_job_gets_the_exact_files(api_as, db, storage_as, dispatcher, tmp_path):
    oid, ovid, cvid, _ = setup(api_as, db, storage_as, tmp_path)
    settings = get_settings().model_copy(update={"work_root": str(tmp_path / "work")})
    job = api_as("creator").post(f"/api/v1/outfits/{oid}/versions/{ovid}/fits",
                                 json={"character_version_id": cvid}).json()["job"]
    out = Supervisor(settings, work_storage=storage_as("worker", "work"), dispatcher=dispatcher,
                     command=FAKE).run(uuid.UUID(job["id"]))
    with db() as s:
        j = s.get(Job, uuid.UUID(job["id"]))
        assert out.status == JobStatus.succeeded, j.error
        assert j.result["data"]["inputs"] == ["character.blend", "character.json", "outfit.blend"]
        assert j.result["recorded"] and j.result["status"] == "verified"


def test_a_swapped_file_fails_the_fit_job(api_as, db, storage_as, dispatcher, tmp_path):
    oid, ovid, cvid, ofiles = setup(api_as, db, storage_as, tmp_path)
    swapped = tmp_path / "swapped.blend"
    swapped.write_text("someone else's outfit")
    storage_as("worker", "work").put_file(ofiles[0]["storage_key"], swapped, "application/octet-stream")
    settings = get_settings().model_copy(update={"work_root": str(tmp_path / "work")})
    job = api_as("creator").post(f"/api/v1/outfits/{oid}/versions/{ovid}/fits",
                                 json={"character_version_id": cvid}).json()["job"]
    out = Supervisor(settings, work_storage=storage_as("worker", "work"), dispatcher=dispatcher,
                     command=FAKE).run(uuid.UUID(job["id"]))
    with db() as s:
        j = s.get(Job, uuid.UUID(job["id"]))
        assert out.status == JobStatus.failed and j.error["kind"] == "refused"
        assert "outfit.blend" in j.error["message"] and "SHA-256" in j.error["message"]
    fit = api_as("reviewer").get(f"/api/v1/outfits/{oid}/versions/{ovid}/fits/{cvid}").json()
    assert fit["status"] == "candidate"  # no verdict without a check
