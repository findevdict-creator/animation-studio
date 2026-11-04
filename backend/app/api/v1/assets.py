"""Generic asset registry reads: versions and their files (any kind)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.schemas import AssetFileOut, AssetVersionOut, DownloadOut
from app.core.auth import Principal, require
from app.core.database import get_session
from app.core.errors import ApiError
from app.core.storage import Storage
from app.models.assets import AssetFile, AssetVersion

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/versions/{version_id}", response_model=AssetVersionOut)
def get_version(version_id: uuid.UUID, session: Session = Depends(get_session),
                _: Principal = Depends(require("asset.read"))) -> AssetVersionOut:
    v = session.get(AssetVersion, version_id)
    if v is None:
        raise ApiError(404, "asset_version_not_found", str(version_id))
    return AssetVersionOut.model_validate(v)


@router.get("/files/{file_id}", response_model=AssetFileOut)
def get_file(file_id: uuid.UUID, session: Session = Depends(get_session),
                _: Principal = Depends(require("asset.read"))) -> AssetFileOut:
    f = session.get(AssetFile, file_id)
    if f is None:
        raise ApiError(404, "asset_file_not_found", str(file_id))
    return AssetFileOut.model_validate(f)


@router.get("/files/{file_id}/download", response_model=DownloadOut, summary="Short-lived presigned URL")
def download_file(file_id: uuid.UUID, session: Session = Depends(get_session),
                _: Principal = Depends(require("asset.read"))) -> DownloadOut:
    f = session.get(AssetFile, file_id)
    if f is None:
        raise ApiError(404, "asset_file_not_found", str(file_id))
    v = session.get(AssetVersion, f.version_id)
    bucket = "library" if v and v.status in ("published", "deprecated") else "work"
    store = Storage.for_bucket(bucket)
    return DownloadOut(url=store.presign_get(f.storage_key, filename=f.storage_key.rsplit("/", 1)[-1]),
                       expires_in_s=store.presign_expiry_s)
