"""Dialogue clips for lip sync (SRS 30 Phase 6): ``POST /audio-clips`` turns an audio upload
into AUDIO-NNN and submits its lipsync job on the media queue; the job normalises the
recording and finds its mouth cues. A scene's dialogue line names a ready clip by code."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.jobs import _out as job_out
from app.api.v1.jobs import submit_and_dispatch
from app.api.v1.schemas import DownloadOut, JobOut, Page
from app.assets import audio as svc
from app.core.auth import Principal, audit, require
from app.core.database import get_session
from app.core.errors import ApiError
from app.core.storage import Storage
from app.models.audio import AudioClip
from app.models.jobs import Job

router = APIRouter(prefix="/audio-clips", tags=["dialogue audio"])


class AudioCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: uuid.UUID = Field(description="an upload of kind audio")
    name: str | None = Field(default=None, max_length=120)
    dialog_text: str | None = Field(default=None, max_length=2000,
                                    description="the transcript; the analyser uses it to choose mouth shapes")


class CueOut(BaseModel):
    start_s: float
    end_s: float
    viseme: str
    shape: str


class AudioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    upload_id: uuid.UUID
    dialog_text: str | None
    status: str = Field(description="analysing | ready | failed")
    job_id: uuid.UUID | None
    wav_sha256: str | None
    duration_s: float | None
    analyser: str | None = Field(description="rhubarb-<version>, or studio-phonetic-1.0 (built-in fallback)")
    recognizer: str | None
    cues: list[CueOut] | None
    error: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None


class AudioJobOut(BaseModel):
    clip: AudioOut
    job: JobOut


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=AudioJobOut,
             summary="Make a dialogue clip from an audio upload and find its mouth cues")
def create(body: AudioCreate, response: Response, session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.create")),
           idempotency_key: str | None = Header(default=None, max_length=200)) -> AudioJobOut:
    replay = idempotency_key and session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if replay:
        clip = session.scalar(select(AudioClip).where(AudioClip.job_id == replay.id))
        if clip is None:
            raise ApiError(409, "idempotency_conflict", "that key was used for another request")
        response.status_code = status.HTTP_200_OK
        return AudioJobOut(clip=AudioOut.model_validate(clip), job=job_out(replay))
    clip = svc.create_clip(session, body.upload_id, body.name, body.dialog_text, who.user_id)
    audit(session, who, "audio.created", "audio_clip", clip.id, code=clip.code, upload_id=str(body.upload_id))
    params: dict[str, Any] = {"audio_id": str(clip.id), "dialog_text": clip.dialog_text}

    def link(job):
        clip.job_id = job.id
    job, _ = submit_and_dispatch(session, who, "lipsync", params, idempotency_key, before_commit=link)
    session.refresh(clip)
    return AudioJobOut(clip=AudioOut.model_validate(clip), job=job_out(job))


@router.get("", response_model=Page[AudioOut], summary="Dialogue clips, newest first")
def list_clips(status_: str | None = Query(default=None, alias="status"), q: str | None = None,
               limit: int = Query(default=50, ge=1, le=200), session: Session = Depends(get_session),
               _: Principal = Depends(require("asset.read"))) -> Page[AudioOut]:
    stmt = select(AudioClip).order_by(AudioClip.id.desc()).limit(limit)
    if status_:
        stmt = stmt.where(AudioClip.status == status_)
    if q:
        stmt = stmt.where(AudioClip.name.ilike(f"%{q}%") | AudioClip.code.ilike(f"%{q}%")
                          | AudioClip.dialog_text.ilike(f"%{q}%"))
    return Page(items=[AudioOut.model_validate(c) for c in session.scalars(stmt)])


@router.get("/{ref}", response_model=AudioOut, summary="A clip by id or code (AUDIO-001)")
def get(ref: str, session: Session = Depends(get_session), _: Principal = Depends(require("asset.read"))) -> AudioOut:
    return AudioOut.model_validate(svc.get_clip(session, ref))


@router.get("/{ref}/dialogue.wav", summary="The normalised recording: 302 to a presigned URL",
            responses={302: {"description": "redirect to the object"}, 200: {"model": DownloadOut}})
def wav(ref: str, redirect: bool = True, session: Session = Depends(get_session),
        _: Principal = Depends(require("asset.read"))):
    clip = svc.get_clip(session, ref)
    if clip.status != "ready":
        raise ApiError(409, "not_ready", f"{clip.code} is {clip.status}")
    store = Storage.for_bucket("work")
    url = store.presign_get(clip.wav_storage_key, filename=f"{clip.code}.wav")
    if redirect:
        return RedirectResponse(url, status_code=302)
    return DownloadOut(url=url, expires_in_s=store.presign_expiry_s)
