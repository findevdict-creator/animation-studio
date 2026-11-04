"""Response and request bodies for the v1 API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


class JobSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    type: str
    queue: str
    status: str
    params: dict[str, Any]
    progress: float
    progress_message: str
    attempt: int
    max_attempts: int
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    worker_host: str | None
    blender_version: str | None
    has_log: bool = False


class JobEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    at: datetime
    kind: str
    message: str
    data: dict[str, Any] | None


class AssetFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_id: uuid.UUID
    role: str
    storage_key: str
    sha256: str
    size_bytes: int
    mime_type: str
    blender_version: str | None
    created_by_job_id: uuid.UUID | None
    created_at: datetime


class AssetVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    asset_id: uuid.UUID
    display_code: str
    status: str
    version_label: str | None
    created_at: datetime
    files: list[AssetFileOut]


class DownloadOut(BaseModel):
    url: str
    expires_in_s: int
