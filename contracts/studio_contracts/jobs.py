"""Job envelopes: the whole interface between a worker and the process it runs.

The worker writes a ``JobEnvelope`` to ``/work/<job_id>/job.json``, starts the process,
reads ``ProgressEvent`` lines from ``progress.jsonl`` and a ``JobResult`` from
``result.json``. The Blender engine never talks to the database or object storage; it only
reads its inputs and writes files under ``out/``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancel_requested = "cancel_requested"
    cancelled = "cancelled"
    timed_out = "timed_out"


TERMINAL_STATUSES = frozenset({JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled, JobStatus.timed_out})


class Queue(StrEnum):
    ai = "ai"
    blender = "blender"
    render = "render"
    media = "media"


class BlenderSmokeParams(Model):
    """Phase 1 acceptance job: Blender builds a small scene and saves it."""

    object_name: str = Field(default="smoke_cube", pattern=r"^[A-Za-z0-9_]{1,63}$")
    size_m: float = Field(default=1.0, gt=0, le=100)


class JobType(StrEnum):
    blender_smoke = "blender_smoke"


JOB_PARAMS: dict[JobType, type[Model]] = {JobType.blender_smoke: BlenderSmokeParams}
JOB_QUEUES: dict[JobType, Queue] = {JobType.blender_smoke: Queue.blender}
#: whether a failed attempt may be retried automatically (it writes only to a fresh draft path)
RETRY_SAFE: dict[JobType, bool] = {JobType.blender_smoke: True}


class InputFile(Model):
    """A file the worker materialised into the workspace before starting the process."""

    path: str  # relative to the workspace
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class JobEnvelope(Model):
    job_id: UUID
    type: JobType
    attempt: int = Field(ge=1)
    params: dict[str, Any]
    inputs: list[InputFile] = Field(default_factory=list)
    contracts_version: str


class ProgressEvent(Model):
    progress: float = Field(ge=0, le=1)
    message: str = ""
    at: datetime | None = None


class OutputFile(Model):
    """A file the process wrote under ``out/``. The worker hashes and uploads it."""

    path: str  # relative to out/
    role: Literal["blend", "spec", "thumbnail", "turntable", "glb", "texture", "manifest", "log", "report"]
    mime_type: str


class JobResult(Model):
    status: Literal["succeeded", "failed"]
    outputs: list[OutputFile] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    blender_version: str | None = None
    engine_version: str | None = None
