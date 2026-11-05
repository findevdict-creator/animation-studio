"""SRS Phase 5 acceptance as a test, with real Blender and real storage.

Ali is generated and published, with two outfits (school, winter) fit-checked, approved and
published, and three accessories (glasses, watch, beanie) generated and published. Two
scenes are then built through the API and the worker:

* "Two Alis": the same character twice, in different outfits, with accessories,
  expressions, clips and dialogue;
* "Winter walk": one Ali in the winter outfit with the beanie.

Each scene version is assembled into its own ``scene.blend`` (the worker stages the exact
pinned library files read-only), then reopened in a fresh workspace and checked against
its manifest. Every library file of every pinned version is byte-identical afterwards."""

import hashlib
import uuid

import pytest
from app.config import get_settings
from app.models.accessories import AccessoryVersion
from app.models.characters import CharacterVersion
from app.models.jobs import Job
from app.models.outfits import OutfitVersion
from studio_contracts.accessory import get_accessory_def
from studio_contracts.jobs import JobStatus
from studio_contracts.outfit import get_garment
from studio_workers.publisher import PublisherRunner
from studio_workers.supervisor import Supervisor

pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")
SMALL = {"resolution": 96, "samples": 2, "turntable_frames": 4, "sheet_tile": 64}
FAST = {"resolution": 96, "samples": 1, "pose_tile": 64}
ACC_FAST = {"resolution": 96, "samples": 1, "glb": False}
SCENE_FAST = {"width": 192, "height": 108, "samples": 1}
ALI = {"name": "Ali", "body": {"age_appearance": "child", "height_m": 1.31,
                               "proportions": {"leg_length": 0.1, "belly": -0.2}}, "hair": {"style": "spiky"}}


def piece(key, **kw):
    g = get_garment(key)
    return {"slot": g.slot, "garment": {"id": g.id, "version": g.version}, **kw}


OUTFITS = {"school": [piece("tshirt", material={"color": "royal_blue"}), piece("shorts"), piece("sneakers")],
           "winter": [piece("sweater", params={"ease": 0.3}), piece("trousers"), piece("boots")]}
ACCESSORIES = ("glasses", "watch", "beanie")


def two_alis(c):
    return {"name": "Two Alis", "frame_start": 1, "frame_end": 48, "camera": {"preset": "wide", "angle_deg": 15},
            "instances": [
                {"key": "ali_school", "character": {"code": c["ali"], "version": "1.0"},
                 "outfit": {"code": c["school"], "version": "1.0"},
                 "accessories": [{"accessory": {"code": c["glasses"], "version": "1.0"}},
                                 {"accessory": {"code": c["watch"], "version": "1.0"}, "attach_point": "wrist_R"}],
                 "transform": {"location": [-0.45, 0, 0], "rotation_deg": [0, 0, 15]},
                 "expressions": [{"frame": 1, "expression": "neutral"}, {"frame": 20, "expression": "happy"}],
                 "animations": [{"clip": "wave", "start_frame": 1}],
                 "dialogue": [{"start_frame": 24, "end_frame": 36, "text": "Hi! It's me, twice."}],
                 "state": {"mood": "cheerful"}},
                {"key": "ali_winter", "character": {"code": c["ali"], "version": "1.0"},
                 "outfit": {"code": c["winter"], "version": "1.0"},
                 "accessories": [{"accessory": {"code": c["beanie"], "version": "1.0"}}],
                 "transform": {"location": [0.45, 0.1, 0], "rotation_deg": [0, 0, -15]},
                 "expressions": [{"frame": 10, "expression": "sad"}, {"frame": 30, "expression": "laughing"}],
                 "animations": [{"clip": "look_around", "start_frame": 5}], "blink": False}]}


def winter_walk(c):
    return {"name": "Winter walk", "frame_start": 1, "frame_end": 36, "camera": {"preset": "medium"},
            "instances": [{"key": "ali", "character": {"code": c["ali"], "version": "1.0"},
                           "outfit": {"code": c["winter"], "version": "1.0"},
                           "accessories": [{"accessory": {"code": c["beanie"], "version": "1.0"}}],
                           "expressions": [{"frame": 12, "expression": "excited", "weight": 0.8}],
                           "animations": [{"clip": "walk", "start_frame": 1, "repeat": 2}]}]}


def test_assemble_and_reopen_scenes(api_as, db, storage_as, dispatcher, tmp_path):
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

    codes, versions = {}, {}
    # Ali, published 1.0
    c = creator.post("/api/v1/characters", json={"name": f"Ali {uuid.uuid4().hex[:6]}", "spec": ALI}).json()
    cid, cvid = c["id"], c["versions"][0]["id"]
    run(creator.post(f"/api/v1/characters/{cid}/drafts/{cvid}/generate", json={"previews": SMALL}).json()["job"])
    assert creator.post(f"/api/v1/characters/{cid}/drafts/{cvid}/submit").status_code == 200
    reviewer.post(f"/api/v1/characters/{cid}/versions/{cvid}/review", json={"decision": "approve"})
    run(director.post(f"/api/v1/characters/{cid}/versions/{cvid}/publish").json()["job"], publisher=True)
    codes["ali"], versions["ali"] = c["code"], (CharacterVersion, cvid)

    for name, pieces in OUTFITS.items():
        o = creator.post("/api/v1/outfits", json={"name": f"{name} {uuid.uuid4().hex[:4]}",
                                                  "spec": {"fit_profiles": ["child_average"], "pieces": pieces}}).json()
        oid, ovid = o["id"], o["versions"][0]["id"]
        base = f"/api/v1/outfits/{oid}"
        assert run(creator.post(f"{base}/drafts/{ovid}/generate", json={"previews": FAST}).json()["job"])["fit_ok"]
        r = creator.post(f"{base}/versions/{ovid}/fits", json={"character_version_id": cvid, "previews": FAST})
        assert run(r.json()["job"])["status"] == "verified"
        reviewer.post(f"{base}/versions/{ovid}/fits/{cvid}/decision", json={"decision": "approve"})
        assert creator.post(f"{base}/drafts/{ovid}/submit").status_code == 200
        reviewer.post(f"{base}/versions/{ovid}/review", json={"decision": "approve"})
        assert run(director.post(f"{base}/versions/{ovid}/publish").json()["job"], publisher=True)["version"] == "1.0"
        codes[name], versions[name] = o["code"], (OutfitVersion, ovid)

    for key in ACCESSORIES:
        d = get_accessory_def(key)
        a = creator.post("/api/v1/accessories", json={"name": f"{key} {uuid.uuid4().hex[:4]}",
                                                      "spec": {"item": {"id": d.id, "version": d.version}}}).json()
        aid, avid = a["id"], a["versions"][0]["id"]
        base = f"/api/v1/accessories/{aid}"
        result = run(creator.post(f"{base}/drafts/{avid}/generate", json={"previews": ACC_FAST}).json()["job"])
        assert result["check_ok"], result["data"]["report"]["failed"]
        assert creator.post(f"{base}/drafts/{avid}/submit").status_code == 200
        reviewer.post(f"{base}/versions/{avid}/review", json={"decision": "approve"})
        assert run(director.post(f"{base}/versions/{avid}/publish").json()["job"], publisher=True)["version"] == "1.0"
        codes[key], versions[key] = a["code"], (AccessoryVersion, avid)

    def library_files():
        with db() as s:
            return {k: {f.storage_key: f.sha256 for f in s.get(m, uuid.UUID(v)).entry.files}
                    for k, (m, v) in versions.items()}

    before = library_files()
    lib = storage_as("api", "library")

    built = []
    for m in (two_alis(codes), winter_walk(codes)):
        r = creator.post("/api/v1/scenes", json={"name": m["name"], "manifest": m})
        assert r.status_code == 201, r.text
        s = r.json()
        base = f"/api/v1/scenes/{s['id']}/versions/{s['versions'][0]['id']}"
        r = creator.post(f"{base}/assemble", json={"previews": SCENE_FAST})
        assert r.status_code == 202, r.text
        result = run(r.json()["job"])
        assert result["recorded"] and result["build_status"] == "built", result
        assert result["accessory_checks_ok"], result["data"]["failed"]
        v = creator.get(base).json()
        assert v["build_status"] == "built" and {f["name"] for f in v["build_files"]} == {
            "scene.blend", "scene_report.json", "frames.png", "thumbnail.png"}
        libs = v["build_report"]["libraries"]["libraries"]
        assert libs and all(p["ok"] and p["stored"].startswith("//../assets/") for p in libs), libs
        built.append((s, base, v))

    for s, base, v in built:
        r = creator.post(f"{base}/reopen", json={"previews": SCENE_FAST})
        assert r.status_code == 202, r.text
        result = run(r.json()["job"])
        assert result["recorded"] and result["verify_status"] == "verified", \
            [c for c in result["data"]["report"]["checks"] if not c["ok"]]
        v = creator.get(base).json()
        assert v["verify_status"] == "verified" and v["verify_report"]["opened_from"] == "scene/scene.blend"
        assert v["verify_report"]["scene_blend_sha256"] == v["scene_blend_sha256"]

    # two scene files, each its own; the same character pinned three times across them
    assert built[0][2]["scene_blend_sha256"] != built[1][2]["scene_blend_sha256"]
    usage = reviewer.get(f"/api/v1/scenes/usage/{cvid}").json()
    assert sorted(u["instance_key"] for u in usage) == ["ali", "ali_school", "ali_winter"]
    # the source assets are unchanged: same rows, same hashes, same stored bytes
    assert library_files() == before
    for files in before.values():
        for key, sha in files.items():
            assert hashlib.sha256(lib.get_bytes(key)).hexdigest() == sha, key
