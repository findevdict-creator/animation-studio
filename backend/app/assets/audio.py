"""Dialogue clips (AUDIO-001): create from an upload, finish from the lipsync job, resolve for
scenes. See ``app.models.audio``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from studio_contracts.audio import LipSync, MouthCue
from studio_contracts.scene import ResolvedAudio, audio_path

from app.core.errors import ApiError
from app.models.audio import AudioClip
from app.models.uploads import Upload


def allocate_code(session: Session) -> str:
    n = session.execute(text(
        "INSERT INTO asset_code_counters (kind, next_number) VALUES ('audio', 2) "
        "ON CONFLICT (kind) DO UPDATE SET next_number = asset_code_counters.next_number + 1 "
        "RETURNING next_number - 1")).scalar_one()
    return f"AUDIO-{n:03d}"


def create_clip(session: Session, upload_id: uuid.UUID, name: str | None, dialog_text: str | None,
                created_by: uuid.UUID | None, kind: str = "dialogue") -> AudioClip:
    up = session.get(Upload, upload_id)
    if up is None:
        raise ApiError(404, "upload_not_found", str(upload_id))
    if up.kind != "audio":
        raise ApiError(422, "not_audio", f"upload {up.id} is a {up.kind} file, not a recording")
    clip = AudioClip(code=allocate_code(session), name=name or up.name, upload_id=up.id, kind=kind, source="upload",
                     dialog_text=((dialog_text or "").strip() or None) if kind == "dialogue" else None,
                     status="analysing", created_by=created_by)
    session.add(clip)
    session.flush()
    return clip


def get_clip(session: Session, ref: str, lock: bool = False) -> AudioClip:
    """By id or by code."""
    try:
        q = select(AudioClip).where(AudioClip.id == uuid.UUID(ref))
    except ValueError:
        q = select(AudioClip).where(AudioClip.code == ref)
    clip = session.scalar(q.with_for_update() if lock else q)
    if clip is None:
        raise ApiError(404, "audio_not_found", ref)
    return clip


def finish(session: Session, clip: AudioClip, job_id: uuid.UUID, wav: dict[str, Any] | None, result: LipSync | None,
           error: str | None = None, provider: str | None = None) -> dict[str, Any]:
    """The lipsync job's outcome, written once."""
    if clip.status != "analysing" or (clip.job_id is not None and clip.job_id != job_id):
        return {"recorded": False, "reason": f"the clip is {clip.status}"}
    clip.job_id = job_id
    clip.finished_at = datetime.now(UTC)
    if result is None or wav is None:
        clip.status, clip.error = "failed", (error or "the analysis failed")[:2000]
        return {"recorded": True, "status": "failed"}
    clip.status = "ready"
    clip.wav_storage_key, clip.wav_sha256 = wav["storage_key"], wav["sha256"]
    clip.duration_s, clip.analyser, clip.recognizer = result.duration_s, result.analyser, result.recognizer
    clip.cues = [c.model_dump() for c in result.cues]
    clip.provider = provider
    return {"recorded": True, "status": "ready", "code": clip.code, "cues": len(result.cues)}


def finish_track(session: Session, clip: AudioClip, job_id: uuid.UUID, wav: dict[str, Any],
                 duration_s: float) -> dict[str, Any]:
    """Music or a sound effect: normalised, no mouth cues."""
    if clip.status != "analysing" or (clip.job_id is not None and clip.job_id != job_id):
        return {"recorded": False, "reason": f"the clip is {clip.status}"}
    clip.job_id, clip.finished_at = job_id, datetime.now(UTC)
    clip.status, clip.wav_storage_key, clip.wav_sha256, clip.duration_s = "ready", wav["storage_key"], wav["sha256"], \
        duration_s
    return {"recorded": True, "status": "ready", "code": clip.code, "kind": clip.kind}


def resolved(clip: AudioClip) -> ResolvedAudio:
    return ResolvedAudio(code=clip.code, sha256=clip.wav_sha256, path=audio_path(clip.code),
                         duration_s=clip.duration_s, analyser=clip.analyser,
                         cues=tuple(MouthCue.model_validate(c) for c in clip.cues))
