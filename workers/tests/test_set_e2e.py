"""Prop and environment library end to end, with real Blender and real storage: a prop and an
environment are made through the API, built and measured by the Blender worker, reviewed,
and published by the publisher, which re-checks the exact files in a fresh process. The
library objects match the manifest, and a published version cannot be published twice."""

import json
import uuid

import pytest
from app.config import get_settings
from app.models.jobs import Job
from studio_contracts.environment import get_environment_def, get_prop_def
from studio_contracts.jobs import JobStatus
from studio_workers.publisher import PublisherRunner
from studio_workers.supervisor import Supervisor

pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")
FAST = {"resolution": 96, "samples": 1, "glb": True}


def test_props_and_environments_publish(api_as, db, storage_as, dispatcher, tmp_path):
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
            return out.status, j.result, j.error

    chair, kitchen = get_prop_def("chair"), get_environment_def("living_room")
    cases = {"props": {"item": {"id": chair.id, "version": chair.version}, "material": {"color": "red"}},
             "environments": {"template": {"id": kitchen.id, "version": kitchen.version}, "lighting": "evening",
                              "marks": [{"name": "rug", "at": (1.0, -1.0), "facing_deg": 20}]}}
    library = storage_as("api", "library")
    for plural, spec in cases.items():
        o = creator.post(f"/api/v1/{plural}", json={"name": f"E2E {uuid.uuid4().hex[:6]}", "spec": spec}).json()
        base, vid = f"/api/v1/{plural}/{o['id']}", o["versions"][0]["id"]
        status, result, error = run(creator.post(f"{base}/drafts/{vid}/generate", json={"previews": FAST}).json()["job"])
        assert status == JobStatus.succeeded, error
        assert result["attached"] and result["check_ok"], result
        v = creator.get(f"{base}/versions/{vid}").json()
        assert v["check_report"]["ok"] and v["ready_for_review"] == []
        assert creator.post(f"{base}/drafts/{vid}/submit").status_code == 200
        reviewer.post(f"{base}/versions/{vid}/review", json={"decision": "approve"})
        status, result, error = run(director.post(f"{base}/versions/{vid}/publish").json()["job"], publisher=True)
        assert status == JobStatus.succeeded, error
        assert result["version"] == "1.0"
        v = creator.get(f"{base}/versions/{vid}").json()
        assert v["status"] == "published"
        manifest = json.loads(library.get_bytes(v["manifest_key"]))
        assert manifest["kind"] == plural[:-1] and manifest["validation"]["publish"]["ok"]
        files = {f["storage_key"].rsplit("/", 1)[-1]: f for f in v["files"]}
        for entry in manifest["files"]:
            assert files[entry["key"]]["sha256"] == entry["sha256"]
        report = json.loads(library.get_bytes(files["publish_validation.json"]["storage_key"]))
        assert report["purpose"] == "publish" and report["ok"]
        if plural == "environments":
            assert manifest["compatibility"]["lighting"] == "evening"
            assert {m["name"] for m in manifest["compatibility"]["layout"]["marks"]} == {"rug", "door"}
        else:
            assert manifest["compatibility"]["size"] == [0.45, 0.47, 0.88]
        # a second publish of the same version is refused before any job runs
        r = director.post(f"{base}/versions/{vid}/publish")
        assert r.status_code == 409 and r.json()["code"] == "invalid_version_status"
