"""Runs one job in one fresh Blender process (architecture 11.1, 11.2).

    claim (compare-and-set) -> workspace -> job.json -> spawn Blender in its own process
    group -> tail progress.jsonl, watch for cancel and timeout -> read result.json ->
    hash and upload outputs -> record outcome -> finish

The Blender process sees only its workspace and a minimal environment: no database URL, no
storage keys, no provider keys. It never talks to anything but the files in its workspace.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

import studio_contracts
from app.config import Settings, get_settings
from app.core.database import get_sessionmaker
from app.core.storage import Storage, sha256_file
from app.jobs import service
from app.jobs.queue import Dispatcher, get_dispatcher
from studio_contracts.jobs import JOB_QUEUES, RETRY_SAFE, JobEnvelope, JobResult, JobStatus, JobType, ProgressEvent
from studio_workers.outcomes import OUTCOMES

log = logging.getLogger("studio.worker")

POLL_S = 0.25
PROGRESS_EVERY_S = 0.5
CANCEL_CHECK_EVERY_S = 1.0
KILL_GRACE_S = 5.0
SAFE_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT")


@dataclass
class RunOutcome:
    status: JobStatus | None  # None: not claimed (duplicate delivery or cancelled before start)
    retried: bool = False


def engine_entrypoint(settings: Settings) -> Path:
    if settings.engine_entrypoint:
        return Path(settings.engine_entrypoint)
    spec = importlib.util.find_spec("cartoon_engine")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("cartoon_engine is not installed; set STUDIO_ENGINE_ENTRYPOINT")
    return Path(next(iter(spec.submodule_search_locations))) / "entrypoint.py"


def blender_command(settings: Settings, job_json: Path) -> list[str]:
    entry = str(engine_entrypoint(settings))
    if settings.blender_mode == "wheel":
        return [sys.executable, entry, str(job_json)]
    return [
        settings.blender_binary, "-b", "--factory-startup", "--disable-autoexec", "-noaudio",
        "--python-exit-code", "1", "--python", entry, "--", str(job_json),
    ]


def child_env(workspace: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in SAFE_ENV if k in os.environ}
    env.update(HOME=str(workspace), TMPDIR=str(workspace / "tmp"), PYTHONNOUSERSITE="1")
    return env


class Supervisor:
    def __init__(self, settings: Settings | None = None, work_storage: Storage | None = None,
                 dispatcher: Dispatcher | None = None, command: list[str] | None = None):
        self.settings = settings or get_settings()
        self._storage = work_storage
        self._dispatcher = dispatcher
        self._command = command  # tests substitute a fake engine
        self.sessions = get_sessionmaker()
        self.host = self.settings.worker_host or socket.gethostname()

    @property
    def storage(self) -> Storage:
        if self._storage is None:
            self._storage = Storage.for_bucket("work", self.settings)
        return self._storage

    @property
    def dispatcher(self) -> Dispatcher:
        return self._dispatcher or get_dispatcher()

    # ------------------------------------------------------------------ run
    def run(self, job_id: uuid.UUID) -> RunOutcome:
        with self.sessions.begin() as s:
            job = service.claim(s, job_id, self.host)
            if job is None:
                log.info("job %s not claimable (already taken, finished or cancelled)", job_id)
                return RunOutcome(None)
            envelope = JobEnvelope(
                job_id=job.id, type=JobType(job.type), attempt=job.attempt, params=job.params,
                contracts_version=studio_contracts.__version__,
            )
            timeout = self.settings.job_timeouts_s.get(job.type, self.settings.default_job_timeout_s)

        workspace = Path(self.settings.work_root) / str(job_id) / f"attempt-{envelope.attempt}"
        if workspace.exists():
            shutil.rmtree(workspace)
        (workspace / "out").mkdir(parents=True)
        (workspace / "tmp").mkdir()
        try:
            envelope.inputs = self._stage_inputs(envelope, workspace)
        except Exception as exc:
            return self._inputs_failed(job_id, envelope, workspace, exc)
        job_json = workspace / "job.json"
        job_json.write_text(envelope.model_dump_json(indent=2))

        status, error, exit_code = self._execute(job_id, workspace, job_json, timeout)
        log_key = self._upload_log(job_id, envelope.attempt, workspace)

        try:
            if status is None:  # process ended on its own: look at what it left
                status, error = self._complete(job_id, envelope, workspace, exit_code, log_key)
                if status is None:
                    return RunOutcome(JobStatus.queued, retried=True)
            else:
                with self.sessions.begin() as s:
                    service.finish(s, job_id, status, error=error, log_key=log_key)
        finally:
            if status == JobStatus.succeeded or not self.settings.keep_failed_workspaces:
                shutil.rmtree(workspace, ignore_errors=True)
        return RunOutcome(status)

    # -------------------------------------------------------------- inputs
    def _stage_inputs(self, envelope: JobEnvelope, workspace: Path) -> list:
        from studio_workers import inputs

        if envelope.type.value not in inputs.STAGERS:
            return []
        storages = {"work": self.storage}
        with self.sessions() as s:
            return inputs.stage(s, envelope.type.value, envelope.params, workspace, self.settings, storages)

    def _inputs_failed(self, job_id: uuid.UUID, envelope: JobEnvelope, workspace: Path, exc: Exception) -> RunOutcome:
        """A refused input fails the job; a storage hiccup is retried like a crashed process."""
        from studio_workers.inputs import InputRefused

        error = {"message": f"inputs: {type(exc).__name__}: {exc}"[:2000],
                 "kind": "refused" if isinstance(exc, InputRefused) else "transient"}
        retried = False
        with self.sessions.begin() as s:
            if not isinstance(exc, InputRefused) and RETRY_SAFE.get(envelope.type, False):
                retried = service.requeue_for_retry(s, job_id, error)
            if not retried:
                service.finish(s, job_id, JobStatus.failed, error=error)
        if not self.settings.keep_failed_workspaces or retried:
            shutil.rmtree(workspace, ignore_errors=True)
        if retried:
            self.dispatcher.send(job_id, JOB_QUEUES[envelope.type].value, countdown_s=5 * 2 ** (envelope.attempt - 1))
            return RunOutcome(JobStatus.queued, retried=True)
        return RunOutcome(JobStatus.failed)

    # -------------------------------------------------------------- process
    def _execute(self, job_id: uuid.UUID, workspace: Path, job_json: Path,
                 timeout_s: float) -> tuple[JobStatus | None, dict[str, Any] | None, int | None]:
        cmd = self._command or blender_command(self.settings, job_json)
        if self._command:
            cmd = [*cmd, str(job_json)]
        log.info("job %s: starting %s", job_id, cmd[0])
        with open(workspace / "blender.log", "wb") as logf:
            proc = subprocess.Popen(cmd, cwd=workspace, env=child_env(workspace), stdout=logf,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            started = time.monotonic()
            tail = _ProgressTail(workspace / "progress.jsonl")
            last_progress = last_cancel_check = 0.0
            while True:
                code = proc.poll()
                now = time.monotonic()
                if now - last_progress >= PROGRESS_EVERY_S or code is not None:
                    last_progress = now
                    ev = tail.latest()
                    if ev is not None:
                        with self.sessions.begin() as s:
                            service.report_progress(s, job_id, ev.progress, ev.message)
                if code is not None:
                    return None, None, code
                if now - started > timeout_s:
                    self._kill(proc)
                    return JobStatus.timed_out, {"message": f"no result after {timeout_s:.0f} s; process killed"}, None
                if now - last_cancel_check >= CANCEL_CHECK_EVERY_S:
                    last_cancel_check = now
                    with self.sessions() as s:
                        if service.cancel_requested(s, job_id):
                            self._kill(proc)
                            return JobStatus.cancelled, {"message": "cancelled by request; process killed"}, None
                time.sleep(POLL_S)

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        for sig, wait in ((signal.SIGTERM, KILL_GRACE_S), (signal.SIGKILL, KILL_GRACE_S)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                return
            try:
                proc.wait(wait)
                return
            except subprocess.TimeoutExpired:
                continue

    # -------------------------------------------------------------- results
    def _complete(self, job_id: uuid.UUID, envelope: JobEnvelope, workspace: Path, exit_code: int | None,
                  log_key: str | None) -> tuple[JobStatus | None, dict[str, Any] | None]:
        result = _read_result(workspace / "result.json")
        if result is None:
            error = {"message": f"process exited with code {exit_code} and no valid result.json",
                     "exit_code": exit_code, "log_key": log_key}
            retried = False
            with self.sessions.begin() as s:
                if RETRY_SAFE.get(envelope.type, False):
                    retried = service.requeue_for_retry(s, job_id, error)
                if not retried:
                    service.finish(s, job_id, JobStatus.failed, error=error, log_key=log_key)
            if retried:
                backoff = 5 * 2 ** (envelope.attempt - 1)
                self.dispatcher.send(job_id, JOB_QUEUES[envelope.type].value, countdown_s=backoff)
                return None, error
            return JobStatus.failed, error

        if result.status == "failed":
            error = {"message": result.error or "failed", "exit_code": exit_code}
            with self.sessions.begin() as s:
                service.finish(s, job_id, JobStatus.failed, error=error, log_key=log_key,
                               blender_version=result.blender_version)
            return JobStatus.failed, error

        try:
            uploaded = self._upload_outputs(job_id, envelope.attempt, workspace, result)
        except Exception as exc:  # an output that is missing or escapes out/ fails the job
            error = {"message": f"output upload failed: {type(exc).__name__}: {exc}"}
            with self.sessions.begin() as s:
                service.finish(s, job_id, JobStatus.failed, error=error, log_key=log_key)
            return JobStatus.failed, error

        with self.sessions.begin() as s:
            outcome = OUTCOMES.get(envelope.type.value)
            recorded = outcome(s, job_id, uploaded, result.data, result.blender_version) if outcome else {}
            service.finish(
                s, job_id, JobStatus.succeeded,
                result={"data": result.data, "outputs": uploaded, **recorded,
                        "engine_version": result.engine_version},
                log_key=log_key, blender_version=result.blender_version,
            )
        return JobStatus.succeeded, None

    def _upload_outputs(self, job_id: uuid.UUID, attempt: int, workspace: Path, result: JobResult) -> list[dict[str, Any]]:
        out_dir = (workspace / "out").resolve()
        uploaded = []
        for o in result.outputs:
            rel = PurePosixPath(o.path)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"output path escapes out/: {o.path}")
            path = (out_dir / rel).resolve()
            if out_dir not in path.parents or not path.is_file():
                raise ValueError(f"declared output missing or outside out/: {o.path}")
            sha = sha256_file(path)
            key = f"jobs/{job_id}/attempt-{attempt}/out/{rel.as_posix()}"
            self.storage.put_file(key, path, o.mime_type, sha256=sha)
            uploaded.append({"path": rel.as_posix(), "role": o.role, "mime_type": o.mime_type, "storage_key": key,
                             "sha256": sha, "size_bytes": path.stat().st_size})
        return uploaded

    def _upload_log(self, job_id: uuid.UUID, attempt: int, workspace: Path) -> str | None:
        path = workspace / "blender.log"
        if not path.exists():
            return None
        key = f"jobs/{job_id}/attempt-{attempt}/blender.log"
        try:
            self.storage.put_file(key, path, "text/plain; charset=utf-8")
        except Exception:
            log.exception("job %s: could not upload the process log", job_id)
            return None
        return key


def _read_result(path: Path) -> JobResult | None:
    try:
        return JobResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None


class _ProgressTail:
    """Reads new complete lines of progress.jsonl and keeps the latest valid event."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.buffer = b""

    def latest(self) -> ProgressEvent | None:
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
        except FileNotFoundError:
            return None
        self.offset += len(chunk)
        self.buffer += chunk
        *lines, self.buffer = self.buffer.split(b"\n")
        latest = None
        for line in lines:
            try:
                latest = ProgressEvent.model_validate(json.loads(line))
            except (ValueError, ValidationError):
                continue
        return latest
