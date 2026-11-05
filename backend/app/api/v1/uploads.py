"""Uploads (SRS 30 Phase 6): motion-capture takes for the animation library and dialogue
recordings for lip sync.

``POST /uploads?kind=bvh&name=walk.bvh`` takes the file as the raw request body (no
multipart), stores it write-once in the work bucket at ``uploads/<id>/<name>`` and records its
SHA-256. A specification names an upload by that key and hash; every job that reads it
checks the hash again.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from studio_contracts.ids import uuid7

from app.core.auth import Principal, audit, require
from app.core.database import get_session
from app.core.errors import ApiError
from app.core.storage import Storage
from app.models.uploads import Upload

router = APIRouter(prefix="/uploads", tags=["uploads"])

LIMITS = {"bvh": 20 * 1024 * 1024, "audio": 25 * 1024 * 1024}
SUFFIXES = {"bvh": (".bvh",), "audio": (".wav", ".mp3", ".ogg", ".flac", ".m4a")}
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class UploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    name: str
    storage_key: str
    sha256: str
    size_bytes: int
    mime_type: str
    uploaded_by: uuid.UUID | None
    created_at: datetime


def _check_content(kind: str, head: bytes) -> None:
    if kind == "bvh" and not head.lstrip().upper().startswith(b"HIERARCHY"):
        raise ApiError(422, "not_a_bvh", "a BVH file starts with HIERARCHY")
    if kind == "audio" and not (head[:4] in (b"RIFF", b"OggS", b"fLaC", b"ID3\x03", b"ID3\x04") or head[:2] == b"\xff\xfb"
                                or head[4:8] == b"ftyp"):
        raise ApiError(422, "not_audio", "the file is not WAV, MP3, Ogg, FLAC or M4A audio")


@router.post("", status_code=status.HTTP_201_CREATED, response_model=UploadOut,
             summary="Upload a BVH take or a dialogue recording (the raw file as the request body)")
async def upload(request: Request, kind: Literal["bvh", "audio"], name: str = Query(max_length=120),
                 session: Session = Depends(get_session),
                 who: Principal = Depends(require("asset.create"))) -> UploadOut:
    if not NAME_RE.fullmatch(name) or not name.lower().endswith(SUFFIXES[kind]):
        raise ApiError(422, "bad_name", f"name must be letters, digits, . _ - and end in {', '.join(SUFFIXES[kind])}")
    limit = LIMITS[kind]
    h = hashlib.sha256()
    size = 0
    head = b""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        with open(path, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise ApiError(413, "too_large", f"a {kind} upload is at most {limit // (1024 * 1024)} MB")
                if len(head) < 64:
                    head += chunk[:64]
                h.update(chunk)
                f.write(chunk)
        if size == 0:
            raise ApiError(422, "empty", "the request body is empty")
        _check_content(kind, head)
        uid = uuid7()
        key = f"uploads/{uid}/{name}"
        mime = request.headers.get("content-type") or "application/octet-stream"
        sha = h.hexdigest()
        Storage.for_bucket("work").put_immutable(key, path, mime, sha)
    row = Upload(id=uid, kind=kind, name=name, storage_key=key, sha256=sha, size_bytes=size, mime_type=mime[:80],
                 uploaded_by=who.user_id)
    session.add(row)
    audit(session, who, "upload.created", "upload", uid, kind=kind, sha256=sha, size=size)
    session.commit()
    session.refresh(row)
    return UploadOut.model_validate(row)


@router.get("/{upload_id}", response_model=UploadOut)
def get_upload(upload_id: uuid.UUID, session: Session = Depends(get_session),
               _: Principal = Depends(require("asset.read"))) -> UploadOut:
    row = session.get(Upload, upload_id)
    if row is None:
        raise ApiError(404, "upload_not_found", str(upload_id))
    return UploadOut.model_validate(row)
