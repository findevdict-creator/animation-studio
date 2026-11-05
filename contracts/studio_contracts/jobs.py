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


class AnimationPreviewSettings(Model):
    tile: int = Field(default=256, ge=64, le=1024)
    samples: int = Field(default=8, ge=1, le=256)


class GenerateAnimationParams(Model):
    """Blender: a validated AnimationSpecification to an Action on the controls of its rig
    profile, baked (generator) or retargeted (BVH, staged as ``inputs/source.bvh``) on the
    reference body, saved alone to ``animation.blend`` and played on the check bodies."""

    spec: dict[str, Any]
    previews: AnimationPreviewSettings = Field(default_factory=AnimationPreviewSettings)
    draft_version_id: UUID | None = Field(
        default=None, description="the animation draft the build is for; without one a new animation is created")

    @model_validator(mode="after")
    def _spec_is_valid(self) -> GenerateAnimationParams:
        from studio_contracts.animation import (
            AnimationSpecification,
            validate_animation,
        )

        spec = AnimationSpecification.model_validate(self.spec)
        errors = [i for i in validate_animation(spec) if i.severity == "error"]
        if errors:
            raise ValueError("; ".join(f"{i.path}: {i.message}" for i in errors))
        self.spec = spec.model_dump(mode="json")
        return self


class SetPreviewSettings(Model):
    resolution: int = Field(default=512, ge=64, le=2048)
    samples: int = Field(default=12, ge=1, le=256)
    glb: bool = True


class GeneratePropParams(Model):
    """Blender: a validated PropSpecification to one prop object, measured against the size
    the contract computed and rendered for review."""

    spec: dict[str, Any]
    previews: SetPreviewSettings = Field(default_factory=SetPreviewSettings)
    draft_version_id: UUID | None = Field(
        default=None, description="the prop draft the build is for; without one a new prop is created")

    @model_validator(mode="after")
    def _spec_is_valid(self) -> GeneratePropParams:
        from studio_contracts.environment import PropSpecification, validate_prop

        spec = PropSpecification.model_validate(self.spec)
        errors = [i for i in validate_prop(spec) if i.severity == "error"]
        if errors:
            raise ValueError("; ".join(f"{i.path}: {i.message}" for i in errors))
        self.spec = spec.model_dump(mode="json")
        return self


class PublishPropParams(Model):
    """Publisher: an approved prop version in review to an immutable library version; its
    Blender process re-checks the exact ``prop.blend``."""

    version_id: UUID
    bump: Literal["minor", "major"] = "minor"


class GenerateEnvironmentParams(Model):
    """Blender: a validated EnvironmentSpecification to a set (shell, openings, dressing,
    lighting rig, camera marks), with its layout measured again in Blender and a wide render
    and top plan for review."""

    spec: dict[str, Any]
    previews: SetPreviewSettings = Field(default_factory=SetPreviewSettings)
    draft_version_id: UUID | None = Field(
        default=None, description="the environment draft the build is for; without one a new environment is created")

    @model_validator(mode="after")
    def _spec_is_valid(self) -> GenerateEnvironmentParams:
        from studio_contracts.environment import (
            EnvironmentSpecification,
            validate_environment,
        )

        spec = EnvironmentSpecification.model_validate(self.spec)
        errors = [i for i in validate_environment(spec) if i.severity == "error"]
        if errors:
            raise ValueError("; ".join(f"{i.path}: {i.message}" for i in errors))
        self.spec = spec.model_dump(mode="json")
        return self


class PublishEnvironmentParams(Model):
    """Publisher: an approved environment version in review to an immutable library version;
    its Blender process re-checks the exact ``environment.blend``."""

    version_id: UUID
    bump: Literal["minor", "major"] = "minor"


class PublishAnimationParams(Model):
    """Publisher: an approved animation version in review to an immutable library version. Its
    Blender process re-checks the exact ``animation.blend`` (contents, digest stamp, the
    compatibility record and the play check on the reference bodies)."""

    version_id: UUID
    bump: Literal["minor", "major"] = "minor"


class LipSyncParams(Model):
    """Media: normalise an uploaded dialogue recording to 16-bit mono WAV and find its mouth
    cues (Rhubarb Lip Sync). The worker stages the upload as ``inputs/<name>``."""

    audio_id: UUID
    dialog_text: str | None = Field(default=None, max_length=2000,
                                    description="the transcript; it helps the analyser find the phonemes")


class ScenePreviewSettings(Model):
    width: int = Field(default=640, ge=64, le=3840)
    height: int = Field(default=360, ge=36, le=2160)
    samples: int = Field(default=12, ge=1, le=256)
    #: frames for the contact sheet; default: start, two in between, end
    frames: list[int] | None = Field(default=None, max_length=8)
    #: also render every Nth frame of the scene into ``preview.mp4`` (0: no video)
    video_every: int = Field(default=0, ge=0, le=24)


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


class GenerateScriptParams(Model):
    """AI: an episode concept to a ``ScriptSpecification`` (SRS 14). The cast and the
    locations are the series' published codes, given by the API; the model may only use
    those. The result is a suggestion stored as a script version a person edits."""

    episode_id: UUID
    concept: str = Field(min_length=3, max_length=4000)
    target_duration_s: float = Field(default=60.0, gt=5, le=1800)
    provider: str | None = Field(default=None, max_length=120)


class SuggestShotsParams(Model):
    """AI: camera suggestions for a script version's storyboard (SRS 15: the AI suggests
    camera angles and compositions; people keep manual control). The result is a storyboard
    a person edits; the rule-based plan is always available without AI."""

    episode_id: UUID
    script_version_id: UUID
    provider: str | None = Field(default=None, max_length=120)


class SynthesizeSpeechParams(Model):
    """Media: say ``text`` with ``voice`` through the named text-to-speech provider, then
    normalise and lip-sync it like an uploaded recording. The clip row exists already."""

    audio_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    voice: dict[str, Any] = Field(description="VoiceProfile")

    @model_validator(mode="after")
    def _voice_is_valid(self) -> SynthesizeSpeechParams:
        from studio_contracts.episode import VoiceProfile

        self.voice = VoiceProfile.model_validate(self.voice).model_dump(mode="json")
        return self


class RenderSceneParams(Model):
    """Render: reopen a scene version's stored ``scene.blend`` (staged with its pinned files,
    like ``verify_scene``), apply the render settings and render its frames to a PNG
    sequence. One process per shot (SRS 18.2)."""

    render_job_id: UUID
    scene_version_id: UUID
    resolved: dict[str, Any]
    scene_blend_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    settings: dict[str, Any] = Field(description="RenderSettings")
    #: render every frame unless a sub-range is asked for
    frame_start: int | None = Field(default=None, ge=0)
    frame_end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _valid(self) -> RenderSceneParams:
        from studio_contracts.episode import RenderSettings
        from studio_contracts.scene import ResolvedScene

        self.resolved = ResolvedScene.model_validate(self.resolved).model_dump(mode="json")
        self.settings = RenderSettings.model_validate(self.settings).model_dump(mode="json")
        return self


class ExportEpisodeParams(Model):
    """Media: the rendered PNG sequences of a render job's shots, joined with their
    transitions into H.264 and mixed with dialogue, music and sound effects into AAC, as one
    MP4 (SRS 18.1 steps 7 to 10). The worker reads the frames from the render outputs."""

    render_job_id: UUID


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
    generate_animation = "generate_animation"
    publish_animation = "publish_animation"
    lipsync = "lipsync"
    generate_prop = "generate_prop"
    publish_prop = "publish_prop"
    generate_environment = "generate_environment"
    publish_environment = "publish_environment"
    generate_script = "generate_script"
    suggest_shots = "suggest_shots"
    synthesize_speech = "synthesize_speech"
    render_scene = "render_scene"
    export_episode = "export_episode"


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
    JobType.generate_animation: GenerateAnimationParams,
    JobType.publish_animation: PublishAnimationParams,
    JobType.lipsync: LipSyncParams,
    JobType.generate_prop: GeneratePropParams,
    JobType.publish_prop: PublishPropParams,
    JobType.generate_environment: GenerateEnvironmentParams,
    JobType.publish_environment: PublishEnvironmentParams,
    JobType.generate_script: GenerateScriptParams,
    JobType.suggest_shots: SuggestShotsParams,
    JobType.synthesize_speech: SynthesizeSpeechParams,
    JobType.render_scene: RenderSceneParams,
    JobType.export_episode: ExportEpisodeParams,
}
JOB_QUEUES: dict[JobType, Queue] = {JobType.blender_smoke: Queue.blender, JobType.interpret_character: Queue.ai,
                                    JobType.interpret_outfit: Queue.ai,
                                    JobType.generate_character: Queue.blender, JobType.publish_character: Queue.publish,
                                    JobType.generate_outfit: Queue.blender, JobType.fit_outfit: Queue.blender,
                                    JobType.publish_outfit: Queue.publish,
                                    JobType.generate_accessory: Queue.blender, JobType.publish_accessory: Queue.publish,
                                    JobType.assemble_scene: Queue.blender, JobType.verify_scene: Queue.blender,
                                    JobType.generate_animation: Queue.blender,
                                    JobType.publish_animation: Queue.publish, JobType.lipsync: Queue.media,
                                    JobType.generate_prop: Queue.blender, JobType.publish_prop: Queue.publish,
                                    JobType.generate_environment: Queue.blender,
                                    JobType.publish_environment: Queue.publish,
                                    JobType.generate_script: Queue.ai, JobType.suggest_shots: Queue.ai,
                                    JobType.synthesize_speech: Queue.media, JobType.render_scene: Queue.render,
                                    JobType.export_episode: Queue.media}
#: whether a failed attempt may be retried automatically (it writes only to a fresh draft path)
RETRY_SAFE: dict[JobType, bool] = {JobType.blender_smoke: True, JobType.interpret_character: True,
                                   JobType.interpret_outfit: True,
                                   JobType.generate_character: True,
                                   # resumable: the version number is claimed by a write-once marker
                                   JobType.publish_character: True,
                                   JobType.generate_outfit: True, JobType.fit_outfit: True,
                                   JobType.publish_outfit: True,
                                   JobType.generate_accessory: True, JobType.publish_accessory: True,
                                   JobType.assemble_scene: True, JobType.verify_scene: True,
                                   JobType.generate_animation: True, JobType.publish_animation: True,
                                   JobType.lipsync: True, JobType.generate_prop: True,
                                   JobType.publish_prop: True, JobType.generate_environment: True,
                                   JobType.publish_environment: True,
                                   JobType.generate_script: True, JobType.suggest_shots: True,
                                   JobType.synthesize_speech: True, JobType.render_scene: True,
                                   JobType.export_episode: True}
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
                                                   JobType.verify_scene: ("scene.edit",),
                                                   JobType.generate_animation: ("asset.create",),
                                                   JobType.publish_animation: ("asset.publish",),
                                                   JobType.lipsync: ("scene.edit",),
                                                   JobType.generate_prop: ("asset.create",),
                                                   JobType.publish_prop: ("asset.publish",),
                                                   JobType.generate_environment: ("asset.create",),
                                                   JobType.publish_environment: ("asset.publish",),
                                                   JobType.generate_script: ("ai.use", "episode.manage"),
                                                   JobType.suggest_shots: ("ai.use", "episode.manage"),
                                                   JobType.synthesize_speech: ("episode.manage",),
                                                   JobType.render_scene: ("render.submit",),
                                                   JobType.export_episode: ("render.submit",)}


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
    role: Literal["blend", "spec", "thumbnail", "turntable", "glb", "texture", "manifest", "log", "report", "preview",
                  "frame"]
    mime_type: str


class JobResult(Model):
    status: Literal["succeeded", "failed"]
    outputs: list[OutputFile] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    blender_version: str | None = None
    engine_version: str | None = None
