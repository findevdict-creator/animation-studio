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
    publish = "publish"


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


class InterpretOutfitParams(Model):
    """AI: a description (and optional reference images) to a validated OutfitSpecification.
    Scope, fit profiles and owner are the caller's, never the model's."""

    description: str = Field(min_length=3, max_length=4000)
    name: str | None = Field(default=None, max_length=80)
    style_profile: str = Field(default="family_cartoon@1.0", pattern=r"^[a-z][a-z0-9_]*@\d+\.\d+$")
    scope: Literal["character", "body_profile", "multi"] = "body_profile"
    owner_character: str | None = Field(default=None, pattern=r"^CHAR-\d{3,}$")
    fit_profiles: list[str] = Field(min_length=1, max_length=8)
    reference_image_keys: list[str] = Field(default_factory=list, max_length=4)
    provider: str | None = Field(default=None, max_length=120)


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
    def _spec_is_valid(self) -> GenerateCharacterParams:
        from studio_contracts.character import CharacterSpecification, validate_spec

        spec = CharacterSpecification.model_validate(self.spec)
        errors = [i for i in validate_spec(spec) if i.severity == "error"]
        if errors:
            raise ValueError("; ".join(f"{i.path}: {i.message}" for i in errors))
        self.spec = spec.model_dump(mode="json")
        return self


class OutfitPreviewSettings(Model):
    resolution: int = Field(default=384, ge=64, le=2048)
    samples: int = Field(default=12, ge=1, le=256)
    pose_tile: int = Field(default=256, ge=64, le=1024)
    pose_sheet: bool = True
    glb: bool = True


class GenerateOutfitParams(Model):
    """Blender: a validated OutfitSpecification to outfit geometry built on the reference body
    of its primary fit profile, fitted back onto that body and put through the fit check."""

    spec: dict[str, Any]
    previews: OutfitPreviewSettings = Field(default_factory=OutfitPreviewSettings)
    draft_version_id: UUID | None = Field(
        default=None, description="the outfit draft the build is for; without one a new outfit is created")

    @model_validator(mode="after")
    def _spec_is_valid(self) -> GenerateOutfitParams:
        from studio_contracts.outfit import OutfitSpecification, validate_outfit

        spec = OutfitSpecification.model_validate(self.spec)
        errors = [i for i in validate_outfit(spec) if i.severity == "error"]
        if errors:
            raise ValueError("; ".join(f"{i.path}: {i.message}" for i in errors))
        self.spec = spec.model_dump(mode="json")
        return self


class FitOutfitParams(Model):
    """Blender: fit an outfit version onto a character version and run the fit check (the
    compatibility registry records the result). The worker supplies the files as inputs:
    ``inputs/character.blend``, ``inputs/character.json`` and ``inputs/outfit.blend``."""

    outfit_version_id: UUID
    character_version_id: UUID
    previews: OutfitPreviewSettings = Field(default_factory=OutfitPreviewSettings)


class PublishCharacterParams(Model):
    """Publisher: an approved character version in review to an immutable library version.

    The worker runs it with the publisher identity, the only one that can write the library
    bucket; its Blender process re-validates the exact ``character.blend`` being published."""

    version_id: UUID
    bump: Literal["minor", "major"] = "minor"


class PublishOutfitParams(Model):
    """Publisher: an approved outfit version in review to an immutable library version. Its
    Blender process re-checks the exact ``outfit.blend`` (file contents, the digest stamp and
    the fit check on the reference body); the character is never part of an outfit's files."""

    version_id: UUID
    bump: Literal["minor", "major"] = "minor"


class AccessoryPreviewSettings(Model):
    resolution: int = Field(default=384, ge=64, le=2048)
    samples: int = Field(default=12, ge=1, le=256)
    glb: bool = True


class GenerateAccessoryParams(Model):
    """Blender: a validated AccessorySpecification to an accessory built on the reference body
    of its reference fit profile, attached back onto that body and put through the attach check."""

    spec: dict[str, Any]
    previews: AccessoryPreviewSettings = Field(default_factory=AccessoryPreviewSettings)
    draft_version_id: UUID | None = Field(
        default=None, description="the accessory draft the build is for; without one a new accessory is created")

    @model_validator(mode="after")
    def _spec_is_valid(self) -> GenerateAccessoryParams:
        from studio_contracts.accessory import (
            AccessorySpecification,
            validate_accessory,
        )

        spec = AccessorySpecification.model_validate(self.spec)
        errors = [i for i in validate_accessory(spec) if i.severity == "error"]
        if errors:
            raise ValueError("; ".join(f"{i.path}: {i.message}" for i in errors))
        self.spec = spec.model_dump(mode="json")
        return self


class PublishAccessoryParams(Model):
    """Publisher: an approved accessory version in review to an immutable library version. Its
    Blender process re-checks the exact ``accessory.blend`` (contents, digest stamp, attach check)."""

    version_id: UUID
    bump: Literal["minor", "major"] = "minor"


class ScenePreviewSettings(Model):
    width: int = Field(default=640, ge=64, le=3840)
    height: int = Field(default=360, ge=36, le=2160)
    samples: int = Field(default=12, ge=1, le=256)
    #: frames for the contact sheet; default: start, two in between, end
    frames: list[int] | None = Field(default=None, max_length=8)


class AssembleSceneParams(Model):
    """Blender: build ``scene.blend`` for a scene version from its resolved manifest. The
    worker stages every pinned file under ``assets/<kind>/<code>/<version>/`` (SHA-256 checked
    against the registry); the job links them, saves ``out/scene.blend`` with relative library
    paths and fails if any staged file changed."""

    scene_version_id: UUID
    resolved: dict[str, Any] = Field(description="ResolvedScene, computed by the API from the manifest")
    previews: ScenePreviewSettings = Field(default_factory=ScenePreviewSettings)

    @model_validator(mode="after")
    def _resolved_is_valid(self) -> AssembleSceneParams:
        from studio_contracts.scene import ResolvedScene

        self.resolved = ResolvedScene.model_validate(self.resolved).model_dump(mode="json")
        return self


class VerifySceneParams(Model):
    """Blender: reopen a saved ``scene.blend`` in a fresh workspace rebuilt from the manifest
    (``scene/scene.blend`` plus the pinned files) and check it against the manifest."""

    scene_version_id: UUID
    resolved: dict[str, Any]
    scene_blend_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previews: ScenePreviewSettings = Field(default_factory=ScenePreviewSettings)

    @model_validator(mode="after")
    def _resolved_is_valid(self) -> VerifySceneParams:
        from studio_contracts.scene import ResolvedScene

        self.resolved = ResolvedScene.model_validate(self.resolved).model_dump(mode="json")
        return self


class JobType(StrEnum):
    blender_smoke = "blender_smoke"
    interpret_character = "interpret_character"
    interpret_outfit = "interpret_outfit"
    generate_character = "generate_character"
    publish_character = "publish_character"
    generate_outfit = "generate_outfit"
    fit_outfit = "fit_outfit"
    publish_outfit = "publish_outfit"
    generate_accessory = "generate_accessory"
    publish_accessory = "publish_accessory"
    assemble_scene = "assemble_scene"
    verify_scene = "verify_scene"


JOB_PARAMS: dict[JobType, type[Model]] = {
    JobType.blender_smoke: BlenderSmokeParams,
    JobType.interpret_character: InterpretCharacterParams,
    JobType.interpret_outfit: InterpretOutfitParams,
    JobType.generate_character: GenerateCharacterParams,
    JobType.publish_character: PublishCharacterParams,
    JobType.generate_outfit: GenerateOutfitParams,
    JobType.fit_outfit: FitOutfitParams,
    JobType.publish_outfit: PublishOutfitParams,
    JobType.generate_accessory: GenerateAccessoryParams,
    JobType.publish_accessory: PublishAccessoryParams,
    JobType.assemble_scene: AssembleSceneParams,
    JobType.verify_scene: VerifySceneParams,
}
JOB_QUEUES: dict[JobType, Queue] = {JobType.blender_smoke: Queue.blender, JobType.interpret_character: Queue.ai,
                                    JobType.interpret_outfit: Queue.ai,
                                    JobType.generate_character: Queue.blender, JobType.publish_character: Queue.publish,
                                    JobType.generate_outfit: Queue.blender, JobType.fit_outfit: Queue.blender,
                                    JobType.publish_outfit: Queue.publish,
                                    JobType.generate_accessory: Queue.blender, JobType.publish_accessory: Queue.publish,
                                    JobType.assemble_scene: Queue.blender, JobType.verify_scene: Queue.blender}
#: whether a failed attempt may be retried automatically (it writes only to a fresh draft path)
RETRY_SAFE: dict[JobType, bool] = {JobType.blender_smoke: True, JobType.interpret_character: True,
                                   JobType.interpret_outfit: True,
                                   JobType.generate_character: True,
                                   # resumable: the version number is claimed by a write-once marker
                                   JobType.publish_character: True,
                                   JobType.generate_outfit: True, JobType.fit_outfit: True,
                                   JobType.publish_outfit: True,
                                   JobType.generate_accessory: True, JobType.publish_accessory: True,
                                   JobType.assemble_scene: True, JobType.verify_scene: True}
#: permissions a caller needs on top of job.submit to submit the type
JOB_PERMISSIONS: dict[JobType, tuple[str, ...]] = {JobType.interpret_character: ("ai.use", "asset.create"),
                                                   JobType.interpret_outfit: ("ai.use", "asset.create"),
                                                   JobType.generate_character: ("asset.create",),
                                                   JobType.publish_character: ("asset.publish",),
                                                   JobType.generate_outfit: ("asset.create",),
                                                   JobType.fit_outfit: ("asset.create",),
                                                   JobType.publish_outfit: ("asset.publish",),
                                                   JobType.generate_accessory: ("asset.create",),
                                                   JobType.publish_accessory: ("asset.publish",),
                                                   JobType.assemble_scene: ("scene.edit",),
                                                   JobType.verify_scene: ("scene.edit",)}


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
