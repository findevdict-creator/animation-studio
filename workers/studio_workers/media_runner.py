"""Runs media jobs (queue ``media``) in the worker process: ffmpeg, the lip-sync analyser and
the speech provider, no Blender (architecture R10).

* ``lipsync``: download the upload (SHA-256 checked) -> normalise -> for dialogue, find the
  mouth cues -> store under the job's output prefix -> finish the clip and the job in one
  transaction. Music and sound effects are normalised to 48 kHz stereo and have no cues.
* ``synthesize_speech``: speak the clip's text with its voice through the provider ->
  normalise -> mouth cues -> finish the clip (which records the provider and its version).
* ``export_episode``: download every shot's frames and every audio clip (SHA-256 checked)
  -> FFmpeg export (``studio_workers.export``) -> store ``episode.mp4`` and its metadata ->
  record them on the render job and finish it.

Input that cannot be used (an unreadable recording, an unknown voice, a failing export)
fails at once; anything else (storage, a crash) is retried with backoff.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import socket
import subprocess
import tempfile
import uuid
import wave
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.core.database import get_sessionmaker
from app.core.storage import Storage
from app.jobs import service
from app.jobs.queue import Dispatcher, get_dispatcher
from studio_contracts.episode import VoiceProfile
from studio_contracts.jobs import (
    JOB_QUEUES,
    RETRY_SAFE,
    ExportEpisodeParams,
    JobStatus,
    JobType,
    LipSyncParams,
    SynthesizeSpeechParams,
)

from studio_workers import export as X
from studio_workers import lipsync as L
from studio_workers import speech as S
from studio_workers.supervisor import RunOutcome

log = logging.getLogger("studio.worker.media")
TRACK_RATE = 48000
MAX_TRACK_SECONDS = 1800.0


def normalise_track(src: Path, dst: Path, ffmpeg: str = "ffmpeg") -> float:
    """Music or an effect: 48 kHz 16-bit stereo WAV; returns the duration."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:a:0",
           "-ac", "2", "-ar", str(TRACK_RATE), "-sample_fmt", "s16", "-map_metadata", "-1", "-fflags", "+bitexact",
           "-flags:a", "+bitexact", "-f", "wav", str(dst)]
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if run.returncode != 0 or not dst.exists():
        raise L.AudioRefused(f"ffmpeg could not read the track: {run.stderr.strip()[:500]}")
    with wave.open(str(dst)) as w:
        duration = w.getnframes() / float(w.getframerate())
    if not 0 < duration <= MAX_TRACK_SECONDS:
        raise L.AudioRefused(f"the track is {duration:.1f} s; it must be between 0 and {MAX_TRACK_SECONDS:.0f} s")
    return round(duration, 4)


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

    def _ws(self, job_id: uuid.UUID) -> Path:
        root = Path(self.settings.work_root)
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=f"media-{job_id}-", dir=root))

    def _put(self, key: str, path: Path, mime: str) -> dict[str, Any]:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        self.storage.put_file(key, path, mime, sha)
        return {"storage_key": key, "sha256": sha, "size_bytes": path.stat().st_size}

    def run(self, job_id: uuid.UUID) -> RunOutcome:
        with self.sessions.begin() as s:
            job = service.claim(s, job_id, self.host)
            if job is None:
                return RunOutcome(None)
            jtype, attempt, params = JobType(job.type), job.attempt, dict(job.params)
        handler = {JobType.lipsync: self._lipsync_job, JobType.synthesize_speech: self._speech_job,
                   JobType.export_episode: self._export_job}[jtype]
        return handler(job_id, jtype, attempt, params)

    # ------------------------------------------------------------ clips
    def _fail_clip(self, s, clip_id, job_id, message: str) -> None:
        from app.assets import audio as audio_svc
        from app.models.audio import AudioClip

        clip = s.get(AudioClip, clip_id, with_for_update=True)
        if clip is not None:
            audio_svc.finish(s, clip, job_id, None, None, error=message)

    def _lipsync_job(self, job_id, jtype, attempt, raw) -> RunOutcome:
        from app.models.audio import AudioClip
        from app.models.uploads import Upload

        params = LipSyncParams.model_validate(raw)
        with self.sessions.begin() as s:
            clip = s.get(AudioClip, params.audio_id)
            up = s.get(Upload, clip.upload_id) if clip is not None and clip.upload_id else None
            if clip is None or up is None:
                service.finish(s, job_id, JobStatus.failed, error={"message": "the clip or its upload no longer exists",
                                                                   "kind": "permanent"})
                return RunOutcome(JobStatus.failed)
            src, kind = (up.storage_key, up.sha256, up.name), clip.kind
            service.report_progress(s, job_id, 0.1, "normalising the recording")
        ws = self._ws(job_id)
        try:
            raw_path = ws / "inputs" / src[2]
            try:
                self.storage.get_file(src[0], raw_path, expected_sha256=src[1])
                if kind == "dialogue":
                    data, wav, result = self._analyse(job_id, attempt, ws, raw_path, params.dialog_text)
                else:
                    data, wav, result = self._track(job_id, attempt, ws, raw_path)
                data["source_sha256"] = src[1]
            except L.AudioRefused as exc:
                return self._permanent(job_id, str(exc), "AudioRefused", params.audio_id)
            except Exception as exc:
                return self._transient(job_id, jtype, attempt, exc, params.audio_id)
            return self._finish_clip(job_id, params.audio_id, wav, result, data)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def _speech_job(self, job_id, jtype, attempt, raw) -> RunOutcome:
        from app.models.audio import AudioClip

        params = SynthesizeSpeechParams.model_validate(raw)
        voice = VoiceProfile.model_validate(params.voice)
        with self.sessions.begin() as s:
            clip = s.get(AudioClip, params.audio_id)
            if clip is None or clip.source != "tts" or clip.dialog_text != params.text or clip.voice != voice.model_dump(
                    mode="json"):
                service.finish(s, job_id, JobStatus.failed, error={"message": "the speech clip does not match the job",
                                                                   "kind": "permanent"})
                return RunOutcome(JobStatus.failed)
            service.report_progress(s, job_id, 0.1, f"speaking with {voice.provider}")
        ws = self._ws(job_id)
        try:
            try:
                provider = S.provider_for(voice, {"espeak-ng": self.settings.espeak_binary})
                spoken = provider.synthesize(params.text, voice, ws / "inputs" / "spoken.wav")
                name = provider.version()
                data, wav, result = self._analyse(job_id, attempt, ws, spoken, params.text)
                data["provider"], data["voice"] = name, voice.model_dump(mode="json")
            except (S.SpeechRefused, L.AudioRefused) as exc:
                return self._permanent(job_id, str(exc), type(exc).__name__, params.audio_id)
            except Exception as exc:
                return self._transient(job_id, jtype, attempt, exc, params.audio_id)
            return self._finish_clip(job_id, params.audio_id, wav, result, data, provider=name)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def _analyse(self, job_id, attempt, ws: Path, raw: Path, text: str | None):
        wav = ws / "out" / "dialogue.wav"
        duration = L.normalise(raw, wav, self.settings.ffmpeg_binary)
        with self.sessions.begin() as s:
            service.report_progress(s, job_id, 0.4, "finding the mouth shapes")
        result = L.analyse(wav, duration, text, ws, self.settings.rhubarb_binary)
        cues = ws / "out" / "cues.json"
        cues.write_text(json.dumps(result.model_dump(mode="json"), indent=1, sort_keys=True))
        prefix = f"jobs/{job_id}/attempt-{attempt}/out"
        stored = self._put(f"{prefix}/dialogue.wav", wav, "audio/wav")
        self.storage.put_file(f"{prefix}/cues.json", cues, "application/json")
        visemes = sorted({c.viseme for c in result.cues} - {"rest"})
        return ({"lipsync": result.model_dump(mode="json"), "dialogue_wav": stored, "visemes": visemes},
                stored, result)

    def _track(self, job_id, attempt, ws: Path, raw: Path):
        wav = ws / "out" / "track.wav"
        duration = normalise_track(raw, wav, self.settings.ffmpeg_binary)
        stored = self._put(f"jobs/{job_id}/attempt-{attempt}/out/track.wav", wav, "audio/wav")
        return {"track_wav": stored, "duration_s": duration}, stored, duration

    def _finish_clip(self, job_id, clip_id, wav, result, data, provider: str | None = None) -> RunOutcome:
        from app.assets import audio as audio_svc
        from app.models.audio import AudioClip

        with self.sessions.begin() as s:
            clip = s.get(AudioClip, clip_id, with_for_update=True)
            if service.cancel_requested(s, job_id):
                audio_svc.finish(s, clip, job_id, None, None, error="cancelled by request")
                service.finish(s, job_id, JobStatus.cancelled, error={"message": "cancelled by request"})
                return RunOutcome(JobStatus.cancelled)
            if isinstance(result, float):
                recorded = audio_svc.finish_track(s, clip, job_id, wav, result)
            else:
                recorded = audio_svc.finish(s, clip, job_id, wav, result, provider=provider)
            service.finish(s, job_id, JobStatus.succeeded, result={"data": data, **recorded})
        return RunOutcome(JobStatus.succeeded)

    # ------------------------------------------------------------ export
    def _export_job(self, job_id, jtype, attempt, raw) -> RunOutcome:
        from app.assets import renders

        params = ExportEpisodeParams.model_validate(raw)
        with self.sessions.begin() as s:
            rj = renders.get(s, str(params.render_job_id))
            try:
                plan = renders.export_plan(s, rj)
            except ValueError as exc:
                service.finish(s, job_id, JobStatus.failed, error={"message": str(exc), "kind": "permanent"})
                return RunOutcome(JobStatus.failed)
            service.report_progress(s, job_id, 0.02, "downloading frames")
        ws = self._ws(job_id)
        try:
            try:
                total = sum(len(sh["frames"]) for sh in plan["shots"])
                n = 0
                for sh in plan["shots"]:
                    for f in sh["frames"]:
                        name = f["path"].rsplit("/", 1)[-1]
                        self.storage.get_file(f["storage_key"], ws / "shots" / sh["shot_id"] / name,
                                              expected_sha256=f["sha256"])
                        n += 1
                    with self.sessions.begin() as s:
                        service.report_progress(s, job_id, 0.02 + 0.4 * n / total, f"downloaded {n}/{total} frames")
                for code, clip in plan["clips"].items():
                    self.storage.get_file(clip["storage_key"], ws / "audio" / f"{code}.wav",
                                          expected_sha256=clip["sha256"])
                with self.sessions.begin() as s:
                    service.report_progress(s, job_id, 0.45, "encoding H.264 and mixing the audio")
                done = X.export(plan, ws, self.settings.ffmpeg_binary, self.settings.ffprobe_binary)
                prefix = f"jobs/{job_id}/attempt-{attempt}/out"
                meta = {**done["meta"], "render_job": plan["render_job"], "episode_version_id": plan["episode_version_id"],
                        "title": plan["title"], "settings": plan["settings"], "asset_versions": plan["asset_versions"],
                        "blender_version": plan["blender_version"],
                        "shots": [{"shot_id": sh["shot_id"], "frames": [f["sha256"] for f in sh["frames"]]}
                                  for sh in plan["shots"]],
                        "audio_clips": {c: v["sha256"] for c, v in plan["clips"].items()}}
                (ws / "out" / "episode_metadata.json").write_text(json.dumps(meta, indent=1, sort_keys=True))
                video = self._put(f"{prefix}/episode.mp4", done["video"], "video/mp4")
                meta_file = self._put(f"{prefix}/episode_metadata.json", ws / "out" / "episode_metadata.json",
                                      "application/json")
            except X.ExportFailed as exc:
                return self._permanent(job_id, str(exc), "ExportFailed")
            except Exception as exc:
                return self._transient(job_id, jtype, attempt, exc)
            with self.sessions.begin() as s:
                if service.cancel_requested(s, job_id):
                    service.finish(s, job_id, JobStatus.cancelled, error={"message": "cancelled by request"})
                    renders.refresh(s, renders.get(s, str(params.render_job_id), lock=True))
                    return RunOutcome(JobStatus.cancelled)
                recorded = renders.record_export(s, job_id, params.render_job_id, video, meta,
                                                 [{"kind": "report", **meta_file}])
                service.finish(s, job_id, JobStatus.succeeded, result={"data": {"meta": meta, "video": video},
                                                                        **recorded})
                renders.refresh(s, renders.get(s, str(params.render_job_id), lock=True))
            return RunOutcome(JobStatus.succeeded)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    # ------------------------------------------------------------ endings
    def _permanent(self, job_id, message: str, kind: str, clip_id=None) -> RunOutcome:
        with self.sessions.begin() as s:
            if clip_id is not None:
                self._fail_clip(s, clip_id, job_id, message)
            service.finish(s, job_id, JobStatus.failed, error={"message": message[:2000], "kind": kind})
            self._refresh_render(s, job_id)
        return RunOutcome(JobStatus.failed)

    def _transient(self, job_id, jtype, attempt, exc, clip_id=None) -> RunOutcome:
        log.warning("job %s attempt %d: %s: %s", job_id, attempt, type(exc).__name__, exc)
        error = {"message": f"{type(exc).__name__}: {exc}"[:2000], "kind": "transient"}
        with self.sessions.begin() as s:
            retried = RETRY_SAFE.get(jtype, False) and service.requeue_for_retry(s, job_id, error)
            if not retried:
                if clip_id is not None:
                    self._fail_clip(s, clip_id, job_id, error["message"])
                service.finish(s, job_id, JobStatus.failed, error=error)
                self._refresh_render(s, job_id)
        if retried:
            (self._dispatcher or get_dispatcher()).send(job_id, JOB_QUEUES[jtype].value,
                                                        countdown_s=5 * 2 ** (attempt - 1))
            return RunOutcome(JobStatus.queued, retried=True)
        return RunOutcome(JobStatus.failed)

    @staticmethod
    def _refresh_render(s, job_id) -> None:
        from app.assets import renders
        from app.models.episodes import RenderJob
        from sqlalchemy import select

        rj = s.scalar(select(RenderJob).where(RenderJob.export_job_id == job_id))
        if rj is not None:
            renders.refresh(s, rj)
