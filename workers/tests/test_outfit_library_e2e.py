"""SRS Phase 4 acceptance as a test, with real Blender and real storage.

One character (Ali) is generated and published. Three outfits are generated for him, each
fit-checked on his published version through the worker (which stages the exact library and
draft files), approved, reviewed and published. Each outfit is its own asset with its own
library files; none of them holds a character, and Ali's published version is untouched."""

import json
import uuid

import pytest

from app.config import get_settings
from app.models.characters import Character, CharacterVersion
from app.models.jobs import Job
from app.models.outfits import OutfitCompatibility
from studio_contracts.jobs import JobStatus
from studio_contracts.outfit import get_garment
from studio_workers.publisher import PublisherRunner
from studio_workers.supervisor import Supervisor

pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")
SMALL = {"resolution": 96, "samples": 2, "turntable_frames": 4, "sheet_tile": 64}
FAST = {"resolution": 96, "samples": 1, "pose_tile": 64}
ALI = {"name": "Ali", "body": {"age_appearance": "child", "height_m": 1.31,
                               "proportions": {"leg_length": 0.1, "belly": -0.2}}, "hair": {"style": "spiky"}}


def piece(key, **kw):
    g = get_garment(key)
    return {"slot": g.slot, "garment": {"id": g.id, "version": g.version}, **kw}


OUTFITS = {
    "school": [piece("tshirt", material={"color": "royal_blue", "trim_color": "white"}), piece("shorts"),
               piece("sneakers", material={"trim_color": "white"}), piece("cap"), piece("backpack")],
    "winter": [piece("sweater", params={"ease": 0.3}, material={"trim_color": "cream"}), piece("trousers"),
               piece("boots")],
    "smart": [piece("longsleeve", material={"color": "red", "trim_color": "white"}),
              piece("trousers", params={"ease": 0.1}, material={"fabric": "cotton", "color": "black"}),
              piece("sneakers", material={"color": "white", "trim_color": "black"})],
}


def test_three_outfits_for_one_character(api_as, db, storage_as, dispatcher, tmp_path):
    settings = get_settings().model_copy(update={"work_root": str(tmp_path / "work"), "blender_mode": "wheel"})
    creator, reviewer, director = api_as("creator"), api_as("reviewer"), api_as("director")

    def run(job, publisher=False):
        if publisher:
            runner = PublisherRunner(settings, work_storage=storage_as("publisher", "work"),
                                     library_storage=storage_as("publisher", "library"), dispatcher=dispatcher)
        else:
            runner = Supervisor(settings, work_storage=storage_as("worker", "work"), dispatcher=dispatcher)
        out = runner.run(uuid.UUID(job["id"]))
        with db() as s:
            j = s.get(Job, uuid.UUID(job["id"]))
            assert out.status == JobStatus.succeeded, j.error
            return j.result

    # Ali, published 1.0
    c = creator.post("/api/v1/characters", json={"name": f"Ali {uuid.uuid4().hex[:6]}", "spec": ALI}).json()
    cid, cvid = c["id"], c["versions"][0]["id"]
    run(creator.post(f"/api/v1/characters/{cid}/drafts/{cvid}/generate", json={"previews": SMALL}).json()["job"])
    assert creator.post(f"/api/v1/characters/{cid}/drafts/{cvid}/submit").status_code == 200
    reviewer.post(f"/api/v1/characters/{cid}/versions/{cvid}/review", json={"decision": "approve"})
    run(director.post(f"/api/v1/characters/{cid}/versions/{cvid}/publish").json()["job"], publisher=True)
    with db() as s:
        before = {f.storage_key: f.sha256 for f in s.get(CharacterVersion, uuid.UUID(cvid)).entry.files}
        characters_before = s.query(Character).count()

    published = {}
    for name, pieces in OUTFITS.items():
        scope = {"scope": "character", "owner_character": c["code"]} if name == "school" else {}
        spec = {"fit_profiles": ["child_average"], "pieces": pieces, **scope}
        o = creator.post("/api/v1/outfits", json={"name": f"{name} {uuid.uuid4().hex[:4]}", "spec": spec}).json()
        oid, ovid = o["id"], o["versions"][0]["id"]
        base = f"/api/v1/outfits/{oid}"
        result = run(creator.post(f"{base}/drafts/{ovid}/generate", json={"previews": FAST}).json()["job"])
        assert result["attached"] and result["fit_ok"], result["data"]["report"]["failed"]

        # fit-check on Ali's published version: the worker stages the library and draft files
        r = creator.post(f"{base}/versions/{ovid}/fits", json={"character_version_id": cvid, "previews": FAST})
        assert r.status_code == 202, r.text
        result = run(r.json()["job"])
        assert result["recorded"] and result["status"] == "verified", result["data"]["report"]["failed"]
        fit = reviewer.get(f"{base}/versions/{ovid}/fits/{cvid}").json()
        assert fit["fit_profile_declared"] and fit["current"]
        assert fit["report"]["wearer"]["character_blend_sha256"] == before[
            f"library/characters/{cid}/versions/1.0/character.blend"]
        assert {f["name"] for f in fit["files"]} == {"fit_report.json", "thumbnail.png", "poses.png", "preview.glb"}
        assert reviewer.post(f"{base}/versions/{ovid}/fits/{cvid}/decision",
                             json={"decision": "approve"}).json()["status"] == "approved"

        assert creator.post(f"{base}/drafts/{ovid}/submit").status_code == 200
        reviewer.post(f"{base}/versions/{ovid}/review", json={"decision": "approve"})
        result = run(director.post(f"{base}/versions/{ovid}/publish").json()["job"], publisher=True)
        assert result["version"] == "1.0"
        published[name] = (oid, ovid, result)

    lib = storage_as("api", "library")
    with db() as s:
        assert s.query(Character).count() == characters_before  # no character was created or copied
        after = {f.storage_key: f.sha256 for f in s.get(CharacterVersion, uuid.UUID(cvid)).entry.files}
        assert after == before  # Ali's published version is untouched
        assert s.query(OutfitCompatibility).filter_by(character_version_id=uuid.UUID(cvid),
                                                      status="approved").count() == 3
    for name, (oid, ovid, result) in published.items():
        prefix = f"library/outfits/{oid}/versions/1.0/"
        manifest = json.loads(lib.get_bytes(prefix + "manifest.json"))
        assert manifest["kind"] == "outfit" and manifest["validation"]["publish"]["ok"]
        names = {f["key"] for f in manifest["files"]}
        assert "outfit.blend" in names and not any(n.startswith("character") for n in names)
        assert [f["character_version_id"] for f in manifest["verified_fits"]] == [cvid]
        if name == "school":
            assert manifest["scope"] == "character" and manifest["owner_character"]["code"] == c["code"]
        report = json.loads(lib.get_bytes(prefix + "publish_validation.json"))
        assert report["ok"] and report["file_checks"][0]["detail"]["armatures"] == []
    # the wardrobe lists all three for Ali's version
    wardrobe = reviewer.get(f"/api/v1/characters/{cid}/versions/{cvid}/outfits", params={"status": "approved"}).json()
    assert {w["outfit_version_id"] for w in wardrobe} == {ovid for _, ovid, _ in published.values()}
