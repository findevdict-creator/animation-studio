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

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class InterpretCharacterParams(Model):
    """AI: a description (and optional reference images) to a validated CharacterSpecification."""

    description: str = Field(min_length=3, max_length=4000)
    name: str | None = Field(default=None, max_length=80, description="keeps the AI from renaming the character")
    style_profile: str = Field(default="family_cartoon@1.0", pattern=r"^[a-z][a-z0-9_]*(@\d+\.\d+)?$")
    reference_image_keys: list[str] = Field(default_factory=list, max_length=4,
                                            description="work-bucket keys under uploads/ (PNG or JPEG)")
    provider: str | None = Field(default=None, max_length=120, description="provider[:model]; default from settings")


class PreviewSettings(Model):
    resolution: int = Field(default=512, ge=64, le=2048)
    samples: int = Field(default=16, ge=1, le=256)
    turntable_frames: int = Field(default=24, ge=4, le=120)
    sheet_tile: int = Field(default=256, ge=64, le=1024)
    turntable: bool = True
    expression_sheet: bool = True
    glb: bool = True


class GenerateCharacterParams(Model):
    """Blender: a validated CharacterSpecification to a rigged character draft with previews."""

    spec: dict[str, Any]
    previews: PreviewSettings = Field(default_factory=PreviewSettings)
    draft_version_id: UUID | None = Field(
        default=None, description="the character draft the build is for; without one a new character is created")

    @model_validator(mode="after")
    def _spec_is_valid(self) -> "GenerateCharacterParams":
        from studio_contracts.character import CharacterSpecification, validate_spec

        spec = CharacterSpecification.model_validate(self.spec)
        errors = [i for i in validate_spec(spec) if i.severity == "error"]
        if errors:
            raise ValueError("; ".join(f"{i.path}: {i.message}" for i in errors))
        self.spec = spec.model_dump(mode="json")
        return self


class JobType(StrEnum):
    blender_smoke = "blender_smoke"
    interpret_character = "interpret_character"
    generate_character = "generate_character"


JOB_PARAMS: dict[JobType, type[Model]] = {
    JobType.blender_smoke: BlenderSmokeParams,
    JobType.interpret_character: InterpretCharacterParams,
    JobType.generate_character: GenerateCharacterParams,
}
JOB_QUEUES: dict[JobType, Queue] = {JobType.blender_smoke: Queue.blender, JobType.interpret_character: Queue.ai,
                                    JobType.generate_character: Queue.blender}
#: whether a failed attempt may be retried automatically (it writes only to a fresh draft path)
RETRY_SAFE: dict[JobType, bool] = {JobType.blender_smoke: True, JobType.interpret_character: True,
                                   JobType.generate_character: True}
#: permissions a caller needs on top of job.submit to submit the type
JOB_PERMISSIONS: dict[JobType, tuple[str, ...]] = {JobType.interpret_character: ("ai.use", "asset.create"),
                                                   JobType.generate_character: ("asset.create",)}


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
    role: Literal["blend", "spec", "thumbnail", "turntable", "glb", "texture", "manifest", "log", "report", "preview"]
    mime_type: str


class JobResult(Model):
    status: Literal["succeeded", "failed"]
    outputs: list[OutputFile] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    blender_version: str | None = None
    engine_version: str | None = None
