"""Agent 4, Director: direction.json"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from studio.ids import (
    AnchorId,
    BeatId,
    CharacterId,
    HexColor,
    LineId,
    OutfitId,
    PropId,
    SceneId,
    ShotId,
    line_shot,
    shot_scene,
)
from studio.schemas.common import StudioModel, VersionedFile

ShotType = Literal["ELS", "LS", "MLS", "MS", "MCU", "CU", "ECU", "OTS", "POV", "INSERT"]
CameraAngle = Literal["eye_level", "low", "high", "birds_eye", "dutch"]
CameraMovement = Literal[
    "static", "pan", "tilt", "dolly_in", "dolly_out", "truck", "crane", "zoom_in", "zoom_out", "handheld"
]
TransitionIn = Literal["cut", "fade_in", "dissolve", "wipe", "match_cut"]
TransitionOut = Literal["cut", "fade_out", "dissolve", "wipe", "match_cut"]


class ColorScriptEntry(StudioModel):
    scene_id: SceneId
    dominant_colors: list[HexColor] = Field(default_factory=list)
    lighting: str = ""


class StyleGuide(StudioModel):
    visual_style: str
    aspect_ratio: str = "16:9"
    fps: int = Field(default=24, gt=0)
    pacing: str = ""
    lens_set_mm: list[float] = Field(default_factory=lambda: [24, 35, 50, 85])
    color_script: list[ColorScriptEntry] = Field(default_factory=list)


class ShotCharacter(StudioModel):
    character_id: CharacterId
    outfit_id: OutfitId
    from_anchor_id: AnchorId | None = None
    to_anchor_id: AnchorId | None = None
    expression: str = "neutral"
    action: str = ""
    action_tags: list[str] = Field(default_factory=list)


class ShotDialogue(StudioModel):
    line_id: LineId
    speaker_id: CharacterId
    text: str = Field(min_length=1)
    emotion: str = ""
    delivery: str = ""
    start_sec: float = Field(default=0.0, ge=0)


class Shot(StudioModel):
    id: ShotId
    order: int = Field(ge=1)
    beat_id: BeatId
    duration_sec: float = Field(gt=0)
    shot_type: ShotType
    camera_angle: CameraAngle = "eye_level"
    camera_movement: CameraMovement = "static"
    subject_id: CharacterId | None = None
    composition: str = ""
    characters: list[ShotCharacter] = Field(default_factory=list)
    prop_ids: list[PropId] = Field(default_factory=list)
    action: str = ""
    lighting: str = ""
    dialogue: list[ShotDialogue] = Field(default_factory=list)
    transition_in: TransitionIn = "cut"
    transition_out: TransitionOut = "cut"

    @model_validator(mode="after")
    def _internal(self) -> "Shot":
        char_ids = {c.character_id for c in self.characters}
        if len(char_ids) != len(self.characters):
            raise ValueError(f"{self.id}: a character appears twice")
        if self.subject_id and self.subject_id not in char_ids:
            raise ValueError(f"{self.id}: subject {self.subject_id} not in characters")
        for d in self.dialogue:
            if line_shot(d.line_id) != self.id:
                raise ValueError(f"{self.id}: line {d.line_id} belongs to another shot")
            if d.speaker_id not in char_ids:
                raise ValueError(f"{self.id}: speaker {d.speaker_id} not in characters")
            if d.start_sec > self.duration_sec:
                raise ValueError(f"{self.id}: line {d.line_id} starts after the shot ends")
        return self


class SceneDirection(StudioModel):
    scene_id: SceneId
    duration_sec: float = Field(gt=0)
    shots: list[Shot] = Field(min_length=1)

    @model_validator(mode="after")
    def _internal(self) -> "SceneDirection":
        ids = [s.id for s in self.shots]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.scene_id}: duplicate shot ids")
        orders = [s.order for s in self.shots]
        if orders != sorted(orders) or len(set(orders)) != len(orders):
            raise ValueError(f"{self.scene_id}: shots must have unique, ascending order")
        for s in self.shots:
            if shot_scene(s.id) != self.scene_id:
                raise ValueError(f"{s.id} does not belong to {self.scene_id}")
        total = sum(s.duration_sec for s in self.shots)
        if abs(total - self.duration_sec) > 0.5:
            raise ValueError(
                f"{self.scene_id}: shots sum to {total:.1f}s but duration_sec is {self.duration_sec:.1f}s"
            )
        return self


class Direction(VersionedFile):
    style_guide: StyleGuide
    scenes: list[SceneDirection] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> "Direction":
        ids = [s.scene_id for s in self.scenes]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate scene_id in direction")
        return self

    def shots(self) -> list[Shot]:
        return [s for sc in self.scenes for s in sc.shots]

    def shot_by_id(self) -> dict[str, Shot]:
        return {s.id: s for s in self.shots()}
