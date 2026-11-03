"""Agent 5, Layout TD: layout.json. The only file Blender reads.

Positions are metres in the environment frame, rotations degrees, times are
frames at the config fps.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from studio.ids import (
    AnchorId,
    BuildRequestId,
    CharacterId,
    ClipId,
    HexColor,
    LibraryId,
    LineId,
    OutfitId,
    PropId,
    SceneId,
    ShotId,
    shot_scene,
)
from studio.schemas.build_requests import BuildRequest
from studio.schemas.common import StudioModel, VersionedFile
from studio.schemas.direction import TransitionIn, TransitionOut

Vec3 = tuple[float, float, float]
CameraRig = Literal["static", "dolly", "crane", "handheld_noise"]
Interpolation = Literal["linear", "bezier", "ease_in_out"]


class EnvironmentRef(StudioModel):
    library_id: LibraryId
    time_variant: str | None = None


class CameraKeyframe(StudioModel):
    frame: int = Field(ge=1)
    position_m: Vec3
    look_at_m: Vec3
    focal_mm: float = Field(gt=0)
    roll_deg: float = 0.0


class DepthOfField(StudioModel):
    focus_target: str
    f_stop: float = Field(gt=0)


class Camera(StudioModel):
    rig: CameraRig = "static"
    sensor_mm: float = Field(default=36, gt=0)
    keyframes: list[CameraKeyframe] = Field(min_length=1)
    interpolation: Interpolation = "ease_in_out"
    depth_of_field: DepthOfField | None = None

    @model_validator(mode="after")
    def _ordered(self) -> "Camera":
        frames = [k.frame for k in self.keyframes]
        if frames != sorted(frames) or len(set(frames)) != len(frames):
            raise ValueError("camera keyframes must be unique and ascending")
        return self


class PathPoint(StudioModel):
    frame: int = Field(ge=1)
    position_m: Vec3
    facing_deg: float = 0.0


class ClipUse(StudioModel):
    clip_id: ClipId
    frame_start: int = Field(ge=1)
    frame_end: int = Field(ge=1)
    blend_frames: int = Field(default=0, ge=0)
    speed: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _range(self) -> "ClipUse":
        if self.frame_end < self.frame_start:
            raise ValueError(f"{self.clip_id}: frame_end before frame_start")
        return self


class ExpressionKey(StudioModel):
    frame: int = Field(ge=1)
    expression: str


class LookAt(StudioModel):
    frame: int = Field(ge=1)
    target: str  # anchor id, character id or prop id


class Attachment(StudioModel):
    prop_id: PropId
    bone: str
    from_frame: int = Field(ge=1)
    to_frame: int | None = None


class LayoutCharacter(StudioModel):
    character_id: CharacterId
    library_id: LibraryId | None = None
    outfit_id: OutfitId
    path: list[PathPoint] = Field(min_length=1)
    clips: list[ClipUse] = Field(default_factory=list)
    expression_keys: list[ExpressionKey] = Field(default_factory=list)
    look_at: list[LookAt] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ordered(self) -> "LayoutCharacter":
        frames = [p.frame for p in self.path]
        if frames != sorted(frames) or len(set(frames)) != len(frames):
            raise ValueError(f"{self.character_id}: path frames must be unique and ascending")
        clips = sorted(self.clips, key=lambda c: c.frame_start)
        for a, b in zip(clips, clips[1:]):
            overlap = a.frame_end - b.frame_start + 1
            if overlap > max(a.blend_frames, b.blend_frames):
                raise ValueError(
                    f"{self.character_id}: clips {a.clip_id} and {b.clip_id} overlap by {overlap} frames"
                )
        return self


class PropKeyframe(StudioModel):
    frame: int = Field(ge=1)
    position_m: Vec3
    rotation_deg: Vec3 = (0.0, 0.0, 0.0)


class LayoutProp(StudioModel):
    prop_id: PropId
    library_id: LibraryId | None = None
    placement: Literal["attached", "static", "animated"]
    keyframes: list[PropKeyframe] = Field(default_factory=list)

    @model_validator(mode="after")
    def _placement(self) -> "LayoutProp":
        if self.placement == "static" and len(self.keyframes) != 1:
            raise ValueError(f"{self.prop_id}: static placement needs exactly one keyframe")
        if self.placement == "animated" and len(self.keyframes) < 2:
            raise ValueError(f"{self.prop_id}: animated placement needs at least two keyframes")
        return self


class Light(StudioModel):
    id: str
    type: Literal["sun", "point", "spot", "area"]
    position_m: Vec3 | None = None
    rotation_deg: Vec3 | None = None
    parent_prop_id: PropId | None = None
    color: HexColor = "#FFFFFF"
    energy: float = Field(gt=0)
    preset: str | None = None


class DialogueTiming(StudioModel):
    line_id: LineId
    speaker_id: CharacterId
    frame_start: int = Field(ge=1)
    frame_end: int = Field(ge=1)

    @model_validator(mode="after")
    def _range(self) -> "DialogueTiming":
        if self.frame_end < self.frame_start:
            raise ValueError(f"{self.line_id}: frame_end before frame_start")
        return self


class ClipGap(StudioModel):
    character_id: CharacterId
    frames: tuple[int, int]
    wanted: str
    build_request_id: BuildRequestId | None = None


class ShotLayout(StudioModel):
    shot_id: ShotId
    scene_id: SceneId
    frame_start: int = Field(ge=1)
    frame_end: int = Field(ge=1)
    environment: EnvironmentRef
    camera: Camera
    characters: list[LayoutCharacter] = Field(default_factory=list)
    props: list[LayoutProp] = Field(default_factory=list)
    lights: list[Light] = Field(default_factory=list)
    dialogue_timing: list[DialogueTiming] = Field(default_factory=list)
    transition_in: TransitionIn = "cut"
    transition_out: TransitionOut = "cut"
    clip_gaps: list[ClipGap] = Field(default_factory=list)

    @property
    def frames(self) -> int:
        return self.frame_end - self.frame_start + 1

    @model_validator(mode="after")
    def _internal(self) -> "ShotLayout":
        if shot_scene(self.shot_id) != self.scene_id:
            raise ValueError(f"{self.shot_id} is not in {self.scene_id}")
        if self.frame_end < self.frame_start:
            raise ValueError(f"{self.shot_id}: frame_end before frame_start")
        lo, hi = self.frame_start, self.frame_end

        def inside(frame: int, what: str) -> None:
            if not lo <= frame <= hi:
                raise ValueError(f"{self.shot_id}: {what} frame {frame} outside {lo}-{hi}")

        for k in self.camera.keyframes:
            inside(k.frame, "camera")
        char_ids = {c.character_id for c in self.characters}
        if len(char_ids) != len(self.characters):
            raise ValueError(f"{self.shot_id}: a character appears twice")
        for c in self.characters:
            for p in c.path:
                inside(p.frame, f"{c.character_id} path")
            for clip in c.clips:
                inside(clip.frame_start, f"{clip.clip_id}")
                inside(clip.frame_end, f"{clip.clip_id}")
            for e in c.expression_keys:
                inside(e.frame, f"{c.character_id} expression")
        for d in self.dialogue_timing:
            inside(d.frame_start, d.line_id)
            inside(d.frame_end, d.line_id)
            if d.speaker_id not in char_ids:
                raise ValueError(f"{self.shot_id}: speaker {d.speaker_id} not in shot")
        light_ids = [l.id for l in self.lights]
        if len(set(light_ids)) != len(light_ids):
            raise ValueError(f"{self.shot_id}: duplicate light ids")
        return self


class Layout(VersionedFile):
    fps: int = Field(gt=0)
    shots: list[ShotLayout] = Field(min_length=1)
    build_requests: list[BuildRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "Layout":
        ids = [s.shot_id for s in self.shots]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate shot_id in layout")
        br = {b.id for b in self.build_requests}
        for s in self.shots:
            for gap in s.clip_gaps:
                if gap.build_request_id and gap.build_request_id not in br:
                    raise ValueError(f"{s.shot_id}: clip gap build request not declared")
        return self

    def shot_by_id(self) -> dict[str, ShotLayout]:
        return {s.shot_id: s for s in self.shots}
