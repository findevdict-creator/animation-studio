"""The worker supervisor against real PostgreSQL, real S3 and (for the smoke job) the real
``bpy`` 5.2 wheel. Failure modes use a fake engine script."""

import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from app.config import get_settings
from app.core.storage import sha256_file
from app.jobs import service
from app.models.assets import AssetFile, AssetVersion
from app.models.jobs import Job
from studio_contracts.jobs import JobStatus
from studio_workers.supervisor import GPU_ENV, Supervisor, blender_command, child_env

FAKE = [sys.executable, str(Path(__file__).with_name("fake_engine.py"))]


@pytest.fixture
def settings(tmp_path):
    s = get_settings().model_copy(update={
        "work_root": str(tmp_path / "work"), "blender_mode": "wheel",
        "job_timeouts_s": {"blender_smoke": 120}, "keep_failed_workspaces": True,
    })
    return s


def new_job(db, **params):
    with db.begin() as s:
        job, _ = service.submit(s, "blender_smoke", params)
        return job.id


def load(db, job_id):
    with db() as s:
        return s.get(Job, job_id)


def sup(settings, storage_as, dispatcher, command=None):
    return Supervisor(settings, work_storage=storage_as("worker", "work"), dispatcher=dispatcher, command=command)


def test_blender_saves_a_file_to_storage(db, settings, storage_as, dispatcher, tmp_path):
    pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel or STUDIO_BLENDER_MODE=binary")
    job_id = new_job(db, size_m=2.0, object_name="acceptance_cube")
    outcome = sup(settings, storage_as, dispatcher).run(job_id)
    job = load(db, job_id)
    assert outcome.status == JobStatus.succeeded, job.error
    assert job.status == "succeeded" and job.progress == 1.0 and job.attempt == 1
    assert job.blender_version.startswith("5.2")
    assert job.result["data"] == {"object": "acceptance_cube", "vertices": 8, "dimensions_m": [2.0, 2.0, 2.0]}

    with db() as s:
        version = s.get(AssetVersion, job.result["asset_version_id"])
        assert version.kind == "test_asset" and version.status == "draft"
        (f,) = version.files
        assert f.role == "blend" and f.created_by_job_id == job_id and f.blender_version.startswith("5.2")
    dest = tmp_path / "back.blend"
    storage_as("api", "work").get_file(f.storage_key, dest, expected_sha256=f.sha256)
    assert dest.read_bytes()[:7] == b"BLENDER"
    assert job.log_key and b"Saved" in storage_as("api", "work").get_bytes(job.log_key)
    assert not (Path(settings.work_root) / str(job_id) / "attempt-1").exists()  # cleaned up on success


def test_a_second_delivery_is_a_no_op(db, settings, storage_as, dispatcher):
    job_id = new_job(db, object_name="fail")
    s = sup(settings, storage_as, dispatcher, FAKE)
    assert s.run(job_id).status == JobStatus.failed
    assert s.run(job_id).status is None
    assert load(db, job_id).attempt == 1


def test_handled_failure_is_not_retried(db, settings, storage_as, dispatcher):
    job_id = new_job(db, object_name="fail")
    assert sup(settings, storage_as, dispatcher, FAKE).run(job_id).status == JobStatus.failed
    job = load(db, job_id)
    assert job.error["message"] == "RuntimeError: deliberate"
    assert dispatcher.sent == []
    assert (Path(settings.work_root) / str(job_id) / "attempt-1" / "blender.log").exists()  # kept for debugging


def test_crash_is_retried_with_backoff_then_fails(db, settings, storage_as, dispatcher):
    job_id = new_job(db, object_name="crash")
    s = sup(settings, storage_as, dispatcher, FAKE)
    first = s.run(job_id)
    assert first.retried and load(db, job_id).status == "queued"
    assert dispatcher.sent == [(str(job_id), "blender", 5)]
    assert s.run(job_id).retried
    assert dispatcher.sent[-1][2] == 10
    assert s.run(job_id).status == JobStatus.failed  # third attempt was the last
    job = load(db, job_id)
    assert job.attempt == 3 and job.status == "failed" and job.error["exit_code"] == 3


def test_garbage_result_counts_as_a_crash(db, settings, storage_as, dispatcher):
    job_id = new_job(db, object_name="garbage")
    assert sup(settings, storage_as, dispatcher, FAKE).run(job_id).retried


def test_cancel_kills_the_process_group(db, settings, storage_as, dispatcher):
    job_id = new_job(db, object_name="hang")
    pid_file = Path(settings.work_root) / str(job_id) / "attempt-1" / "pid"

    def cancel_soon():
        for _ in range(100):
            if pid_file.exists():
                break
            time.sleep(0.1)
        with db.begin() as s:
            service.request_cancel(s, job_id)

    t = threading.Thread(target=cancel_soon)
    t.start()
    started = time.monotonic()
    outcome = sup(settings, storage_as, dispatcher, FAKE).run(job_id)
    t.join()
    assert outcome.status == JobStatus.cancelled
    assert time.monotonic() - started < 15
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    job = load(db, job_id)
    assert job.status == "cancelled" and job.progress == pytest.approx(0.1)


def test_timeout_kills_and_marks_timed_out(db, settings, storage_as, dispatcher):
    settings = settings.model_copy(update={"job_timeouts_s": {"blender_smoke": 1}})
    job_id = new_job(db, object_name="hang")
    started = time.monotonic()
    assert sup(settings, storage_as, dispatcher, FAKE).run(job_id).status == JobStatus.timed_out
    assert time.monotonic() - started < 10
    assert load(db, job_id).status == "timed_out"


def test_outputs_cannot_escape_the_workspace(db, settings, storage_as, dispatcher):
    job_id = new_job(db, object_name="escape")
    assert sup(settings, storage_as, dispatcher, FAKE).run(job_id).status == JobStatus.failed
    assert "escapes out/" in load(db, job_id).error["message"]


def test_the_engine_sees_no_credentials(db, settings, storage_as, dispatcher, monkeypatch):
    monkeypatch.setenv("STUDIO_S3_SECRET_KEY", "do-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "do-not-leak")
    job_id = new_job(db, object_name="envcheck")
    assert sup(settings, storage_as, dispatcher, FAKE).run(job_id).status == JobStatus.succeeded
    data = load(db, job_id).result["data"]
    assert data["leaked"] == []
    assert data["home"].endswith("attempt-1")


def test_binary_command_line(settings):
    s = settings.model_copy(update={"blender_mode": "binary", "blender_binary": "/opt/blender/blender"})
    cmd = blender_command(s, Path("/work/j/job.json"))
    assert cmd[:6] == ["/opt/blender/blender", "-b", "--factory-startup", "--disable-autoexec", "-noaudio", "--python-exit-code"]
    assert cmd[-2:] == ["--", "/work/j/job.json"] and cmd[-3].endswith("cartoon_engine/entrypoint.py")
    assert set(child_env(Path("/w"))) <= {"PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "HOME", "TMPDIR", "PYTHONNOUSERSITE",
                                         *GPU_ENV}


def test_gpu_settings_reach_blender_and_secrets_do_not(monkeypatch):
    monkeypatch.setenv("STUDIO_CYCLES_DEVICE", "optix")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/lib/wsl/lib")
    monkeypatch.setenv("STUDIO_S3_SECRET_KEY", "nope")
    env = child_env(Path("/w"))
    assert env["STUDIO_CYCLES_DEVICE"] == "optix" and env["LD_LIBRARY_PATH"] == "/usr/lib/wsl/lib"
    assert "STUDIO_S3_SECRET_KEY" not in env and not any("KEY" in k or "SECRET" in k for k in GPU_ENV)
