"""Phase 1 acceptance through every real component: HTTP API -> PostgreSQL -> Redis ->
Celery worker -> Blender (bpy 5.2 wheel) -> S3 -> API metadata."""

import os
import shutil
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app.jobs.queue import set_dispatcher
from app.main import create_app
from studio_testing.plugin import make_token


@pytest.fixture
def celery_worker(migrated, s3_buckets, tmp_path):
    pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")
    celery = shutil.which("celery", path=os.path.dirname(sys.executable))
    env = {
        **os.environ,
        "STUDIO_S3_ACCESS_KEY": "dev-worker", "STUDIO_S3_SECRET_KEY": "dev-worker-secret",
        "STUDIO_BLENDER_MODE": "wheel", "STUDIO_WORK_ROOT": str(tmp_path / "work"),
        "STUDIO_WORKER_HOST": "e2e-worker",
    }
    proc = subprocess.Popen(
        [celery, "-A", "studio_workers.tasks", "worker", "-Q", "blender", "-P", "solo", "--loglevel", "WARNING",
         "--hostname", f"e2e-{os.getpid()}@%h"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    yield proc
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_api_to_blender_to_storage_and_back(celery_worker):
    set_dispatcher(None)  # the real Celery dispatcher
    with TestClient(create_app()) as api:
        api.headers["Authorization"] = f"Bearer {make_token('e2e-creator', ['creator'])}"
        r = api.post("/api/v1/jobs", json={"type": "blender_smoke", "params": {"object_name": "e2e_cube"}})
        assert r.status_code == 202, r.text
        job_id = r.json()["id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            job = api.get(f"/api/v1/jobs/{job_id}").json()
            if job["status"] in ("succeeded", "failed", "timed_out", "cancelled"):
                break
            time.sleep(0.5)
        if job["status"] != "succeeded":
            out = celery_worker.stdout.read1().decode() if celery_worker.poll() is not None else ""
            pytest.fail(f"job ended {job['status']}: {job['error']} {out}")
        assert job["worker_host"] == "e2e-worker" and job["has_log"]
        version = api.get(f"/api/v1/assets/versions/{job['result']['asset_version_id']}").json()
        (f,) = version["files"]
        assert f["role"] == "blend" and f["size_bytes"] > 0 and len(f["sha256"]) == 64
        assert f["storage_key"].startswith(f"jobs/{job_id}/attempt-1/out/")
        assert api.get(f"/api/v1/assets/files/{f['id']}/download").status_code == 200
