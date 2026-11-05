"""Runs media jobs (queue ``media``) in the worker process: ffmpeg and the lip-sync analyser,
no Blender (architecture R10).

lipsync: claim -> download the upload (SHA-256 checked) -> normalise to ``dialogue.wav`` ->
find the mouth cues -> store ``dialogue.wav`` and ``cues.json`` under the job's output prefix
in the work bucket -> finish the clip and the job in one transaction.

A recording that cannot be used (unreadable, longer than a minute, silent) fails the clip and
the job at once; anything else (storage, a crash of the analyser) is retried with backoff.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import socket
import tempfile
import uuid
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.core.database import get_sessionmaker
from app.core.storage import Storage
from app.jobs import service
from app.jobs.queue import Dispatcher, get_dispatcher
from studio_contracts.jobs import (
    JOB_QUEUES,
    RETRY_SAFE,
    JobStatus,
    JobType,
    LipSyncParams,
)

from studio_workers import lipsync as L
from studio_workers.supervisor import RunOutcome

log = logging.getLogger("studio.worker.media")


class MediaRunner:
    def __init__(self, settings: Settings | None = None, work_storage: Storage | None = None,
                 dispatcher: Dispatcher | None = None):
        self.settings = settings or get_settings()
        self._storage = work_storage
        self._dispatcher = dispatcher
        self.sessions = get_sessionmaker()
        self.host = self.settings.worker_host or socket.gethostname()

    @property
    def storage(self) -> Storage:
        if self._storage is None:
            self._storage = Storage.for_bucket("work")
        return self._storage

    def run(self, job_id: uuid.UUID) -> RunOutcome:
        from app.assets import audio as audio_svc
        from app.models.audio import AudioClip
        from app.models.uploads import Upload

        with self.sessions.begin() as s:
            job = service.claim(s, job_id, self.host)
            if job is None:
                return RunOutcome(None)
            jtype, attempt = JobType(job.type), job.attempt
            params = LipSyncParams.model_validate(job.params)
            clip = s.get(AudioClip, params.audio_id)
            up = s.get(Upload, clip.upload_id) if clip is not None else None
            if clip is None or up is None:
                service.finish(s, job_id, JobStatus.failed, error={"message": "the dialogue clip no longer exists",
                                                                   "kind": "permanent"})
                return RunOutcome(JobStatus.failed)
            src = (up.storage_key, up.sha256, up.name)
            service.report_progress(s, job_id, 0.1, "normalising the recording")
        root = Path(self.settings.work_root)
        root.mkdir(parents=True, exist_ok=True)
        ws = Path(tempfile.mkdtemp(prefix=f"media-{job_id}-", dir=root))
        try:
            try:
                data, wav = self._lipsync(job_id, attempt, ws, src, params)
            except L.AudioRefused as exc:
                with self.sessions.begin() as s:
                    clip = s.get(AudioClip, params.audio_id, with_for_update=True)
                    audio_svc.finish(s, clip, job_id, None, None, error=str(exc))
                    service.finish(s, job_id, JobStatus.failed, error={"message": str(exc)[:2000],
                                                                       "kind": "AudioRefused"})
                return RunOutcome(JobStatus.failed)
            except Exception as exc:  # transient: retry if attempts remain
                return self._transient(job_id, jtype, attempt, exc, params)
            with self.sessions.begin() as s:
                if service.cancel_requested(s, job_id):
                    clip = s.get(AudioClip, params.audio_id, with_for_update=True)
                    audio_svc.finish(s, clip, job_id, None, None, error="cancelled by request")
                    service.finish(s, job_id, JobStatus.cancelled, error={"message": "cancelled by request"})
                    return RunOutcome(JobStatus.cancelled)
                clip = s.get(AudioClip, params.audio_id, with_for_update=True)
                recorded = audio_svc.finish(s, clip, job_id, wav, L.LipSync.model_validate(data["lipsync"]))
                service.finish(s, job_id, JobStatus.succeeded, result={"data": data, **recorded})
            return RunOutcome(JobStatus.succeeded)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def _lipsync(self, job_id: uuid.UUID, attempt: int, ws: Path, src: tuple[str, str, str],
                 params: LipSyncParams) -> tuple[dict[str, Any], dict[str, Any]]:
        key, sha, name = src
        raw = ws / "inputs" / name
        self.storage.get_file(key, raw, expected_sha256=sha)
        wav = ws / "out" / "dialogue.wav"
        duration = L.normalise(raw, wav, self.settings.ffmpeg_binary)
        with self.sessions.begin() as s:
            service.report_progress(s, job_id, 0.4, "finding the mouth shapes")
        result = L.analyse(wav, duration, params.dialog_text, ws, self.settings.rhubarb_binary)
        cues = ws / "out" / "cues.json"
        cues.write_text(json.dumps(result.model_dump(mode="json"), indent=1, sort_keys=True))
        prefix = f"jobs/{job_id}/attempt-{attempt}/out"
        wav_sha = hashlib.sha256(wav.read_bytes()).hexdigest()
        self.storage.put_file(f"{prefix}/dialogue.wav", wav, "audio/wav", wav_sha)
        self.storage.put_file(f"{prefix}/cues.json", cues, "application/json")
        stored = {"storage_key": f"{prefix}/dialogue.wav", "sha256": wav_sha}
        visemes = sorted({c.viseme for c in result.cues} - {"rest"})
        return {"lipsync": result.model_dump(mode="json"), "dialogue_wav": stored, "visemes": visemes,
                "source_sha256": sha}, stored

    def _transient(self, job_id, jtype, attempt, exc, params) -> RunOutcome:
        from app.assets import audio as audio_svc
        from app.models.audio import AudioClip

        log.warning("job %s attempt %d: %s: %s", job_id, attempt, type(exc).__name__, exc)
        error = {"message": f"{type(exc).__name__}: {exc}"[:2000], "kind": "transient"}
        with self.sessions.begin() as s:
            retried = RETRY_SAFE.get(jtype, False) and service.requeue_for_retry(s, job_id, error)
            if not retried:
                clip = s.get(AudioClip, params.audio_id, with_for_update=True)
                audio_svc.finish(s, clip, job_id, None, None, error=error["message"])
                service.finish(s, job_id, JobStatus.failed, error=error)
        if retried:
            (self._dispatcher or get_dispatcher()).send(job_id, JOB_QUEUES[jtype].value,
                                                        countdown_s=5 * 2 ** (attempt - 1))
            return RunOutcome(JobStatus.queued, retried=True)
        return RunOutcome(JobStatus.failed)
