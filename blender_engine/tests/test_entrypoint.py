"""The entry point as the worker runs it: a separate process with a job.json."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import cartoon_engine
from studio_contracts.ids import uuid7

pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")
ENTRY = Path(cartoon_engine.__file__).with_name("entrypoint.py")


def run(tmp_path, job_type="blender_smoke", params=None):
    job = {"job_id": str(uuid7()), "type": job_type, "attempt": 1, "params": params or {}, "contracts_version": "0.1.0"}
    (tmp_path / "job.json").write_text(json.dumps(job))
    proc = subprocess.run([sys.executable, str(ENTRY), str(tmp_path / "job.json")], capture_output=True, text=True,
                          timeout=120)
    return proc, json.loads((tmp_path / "result.json").read_text())


def test_smoke_job_writes_a_reopenable_blend(tmp_path):
    proc, result = run(tmp_path, params={"object_name": "probe", "size_m": 0.5})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert result["status"] == "succeeded" and result["blender_version"].startswith("5.2")
    assert result["outputs"] == [{"path": "smoke.blend", "role": "blend", "mime_type": "application/x-blender"}]
    assert result["data"]["dimensions_m"] == [0.5, 0.5, 0.5]
    lines = (tmp_path / "progress.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["progress"] == 1.0


def test_bad_params_fail_cleanly(tmp_path):
    proc, result = run(tmp_path, params={"size_m": -1})
    assert proc.returncode == 1
    assert result["status"] == "failed" and "ValidationError" in result["error"]


def test_unknown_job_type_fails_cleanly(tmp_path):
    proc, result = run(tmp_path, job_type="not_a_job")
    assert proc.returncode == 1 and result["status"] == "failed"
