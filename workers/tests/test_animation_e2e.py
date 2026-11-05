"""SRS Phase 6 Animation Library end to end, with real Blender and real storage.

Two characters are published: Ali (a child) and Dev (an adult). Three animations are made
through the API and the worker and published by the publisher:

* a generated walk cycle (every body),
* the CMU subject 2 trial 1 walk, uploaded as a BVH take and retargeted (every body),
* a generated wave declared for child bodies only.

The same walk version is then pinned in two scenes, on Ali and on Dev. Each scene is
assembled, reopened and verified: the action came from the pinned file and was fitted to that
rig's leg length, the root travelled the stride the feet imply, and the planted feet held in
the world. A scene that pins the child-only wave on Dev is refused as incompatible. The
published animation files are byte-identical afterwards."""

import hashlib
import uuid
from pathlib import Path

import pytest
from app.config import get_settings
from app.models.animations import AnimationVersion
from app.models.jobs import Job
from studio_contracts.jobs import JobStatus
from studio_workers.publisher import PublisherRunner
from studio_workers.supervisor import Supervisor

pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")
ROOT = Path(__file__).resolve().parents[2]
CMU_WALK = ROOT / "samples" / "mocap" / "cmu_02_01_walk.bvh"
SMALL = {"resolution": 96, "samples": 2, "turntable_frames": 4, "sheet_tile": 64}
ANIM_FAST = {"tile": 64, "samples": 1}
SCENE_FAST = {"width": 192, "height": 108, "samples": 1}
PEOPLE = {"ali": {"name": "Ali", "body": {"age_appearance": "child", "height_m": 1.31}, "hair": {"style": "spiky"}},
          "dev": {"name": "Dev", "body": {"age_appearance": "adult", "height_m": 1.75}, "hair": {"style": "short"}}}


def gen(gid, **kw):
    return {"source": {"kind": "generator", "generator": {"id": gid, "version": "1.0"}}, **kw}


def test_animations_are_reused_across_scenes_and_rigs(api_as, db, storage_as, dispatcher, tmp_path):
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

    codes = {}
    for key, spec in PEOPLE.items():
        c = creator.post("/api/v1/characters", json={"name": f"{spec['name']} {uuid.uuid4().hex[:6]}",
                                                     "spec": spec}).json()
        cid, cvid = c["id"], c["versions"][0]["id"]
        run(creator.post(f"/api/v1/characters/{cid}/drafts/{cvid}/generate", json={"previews": SMALL}).json()["job"])
        assert creator.post(f"/api/v1/characters/{cid}/drafts/{cvid}/submit").status_code == 200
        reviewer.post(f"/api/v1/characters/{cid}/versions/{cvid}/review", json={"decision": "approve"})
        run(director.post(f"/api/v1/characters/{cid}/versions/{cvid}/publish").json()["job"], publisher=True)
        codes[key] = c["code"]

    # the CMU take goes in as an upload; the spec names it by key and SHA-256
    data = CMU_WALK.read_bytes()
    up = creator.post("/api/v1/uploads", params={"kind": "bvh", "name": CMU_WALK.name}, content=data)
    assert up.status_code == 201, up.text
    up = up.json()
    assert up["sha256"] == hashlib.sha256(data).hexdigest() and up["storage_key"].startswith("uploads/")
    specs = {"walk": gen("GEN-WALK"),
             "mocap": {"category": "locomotion", "source": {
                 "kind": "bvh", "upload_key": up["storage_key"], "sha256": up["sha256"], "skeleton": "cmu",
                 "travels": True, "licence": "CMU Graphics Lab Motion Capture Database (free use)",
                 "source_name": "CMU 02_01 walk"}},
             "wave": gen("GEN-WAVE", fit_profiles=["child_average"])}
    versions = {}
    for key, spec in specs.items():
        a = creator.post("/api/v1/animations", json={"name": f"{key} {uuid.uuid4().hex[:4]}", "spec": spec})
        assert a.status_code == 201, a.text
        a = a.json()
        aid, vid = a["id"], a["versions"][0]["id"]
        base = f"/api/v1/animations/{aid}"
        result = run(creator.post(f"{base}/drafts/{vid}/generate", json={"previews": ANIM_FAST}).json()["job"])
        assert result["check_ok"], result["data"]["report"]["failed"]
        v = creator.get(f"{base}/versions/{vid}").json()
        assert v["ready_for_review"] == [] and v["compat"]["rig_profile"] == "cartoon_humanoid@1.0", v
        assert creator.post(f"{base}/drafts/{vid}/submit").status_code == 200
        reviewer.post(f"{base}/versions/{vid}/review", json={"decision": "approve"})
        assert run(director.post(f"{base}/versions/{vid}/publish").json()["job"], publisher=True)["version"] == "1.0"
        codes[key], versions[key] = a["code"], vid

    def library_files():
        with db() as s:
            return {k: {f.storage_key: f.sha256 for f in s.get(AnimationVersion, uuid.UUID(v)).entry.files}
                    for k, v in versions.items()}

    before = library_files()
    lib = storage_as("api", "library")

    def scene(name, who, uses, frame_end=60, **inst):
        return {"name": name, "frame_start": 1, "frame_end": frame_end, "camera": {"preset": "wide"},
                "instances": [{"key": who, "character": {"code": codes[who], "version": "1.0"},
                               "animations": uses, **inst}]}

    walk_use = {"clip": f"{codes['walk']}@1.0", "start_frame": 1, "repeat": 2}
    manifests = [
        scene("Ali walks and waves", "ali", [walk_use, {"clip": f"{codes['wave']}@1.0", "start_frame": 40}],
              frame_end=72, expressions=[{"frame": 40, "expression": "happy"}],
              dialogue=[{"start_frame": 44, "end_frame": 60, "text": "Hello there!"}]),
        scene("Dev walks", "dev", [walk_use, {"clip": f"{codes['mocap']}@1.0", "start_frame": 60}], frame_end=120),
    ]
    built = []
    for m in manifests:
        r = creator.post("/api/v1/scenes", json={"name": m["name"], "manifest": m})
        assert r.status_code == 201, r.text
        s = r.json()
        base = f"/api/v1/scenes/{s['id']}/versions/{s['versions'][0]['id']}"
        result = run(creator.post(f"{base}/assemble", json={"previews": SCENE_FAST}).json()["job"])
        assert result["recorded"] and result["build_status"] == "built", result
        built.append(base)

    for base in built:
        result = run(creator.post(f"{base}/reopen", json={"previews": SCENE_FAST}).json()["job"])
        checks = result["data"]["report"]["checks"]
        assert result["verify_status"] == "verified", str([c for c in checks if not c["ok"]])
        names = {c["name"].split(":")[1] for c in checks if c["name"].count(":") >= 2}
        assert {"animation", "travels", "feet_planted"} <= names, names

    # the same walk version on two rigs, scaled to each one's legs
    reports = [creator.get(b).json()["verify_report"]["checks"] for b in built]
    ratios = [next(c["leg_ratio"] for c in r if c["name"].endswith(f"animation:{codes['walk']}@1.0")) for r in reports]
    assert abs(ratios[0] - 1.0) < 0.15 and ratios[1] > ratios[0] * 1.2, ratios
    usage = reviewer.get(f"/api/v1/scenes/usage/{versions['walk']}").json()
    assert sorted(u["instance_key"] for u in usage) == ["ali", "dev"]

    # a child-only animation on an adult is refused before anything is built
    r = creator.post("/api/v1/scenes", json={"name": "Dev waves", "manifest": scene(
        "Dev waves", "dev", [{"clip": f"{codes['wave']}@1.0", "start_frame": 1}])})
    assert r.status_code == 422 and "incompatible" in r.text, r.text

    assert library_files() == before
    for files in before.values():
        for key, sha in files.items():
            assert hashlib.sha256(lib.get_bytes(key)).hexdigest() == sha, key
