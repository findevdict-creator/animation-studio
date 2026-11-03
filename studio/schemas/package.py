"""Final output: production_package.json"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from studio.ids import BeatId, LocationId, PropId, SceneId
from studio.schemas.approvals import ApprovalRecord
from studio.schemas.build_requests import BuildRequest
from studio.schemas.characters import Character
from studio.schemas.common import StudioModel, TimeOfDay
from studio.schemas.continuity import ContinuityReport
from studio.schemas.direction import Shot, StyleGuide
from studio.schemas.layout import ShotLayout
from studio.schemas.story_bible import Beat, Setting
from studio.schemas.world import CastMember, Location, Prop

PackageStatus = Literal["ready", "needs_review", "blocked_on_assets"]


class PackageInput(StudioModel):
    story_sha256: str
    config: dict[str, Any]
    library_version: str


class PackageStory(StudioModel):
    title: str
    logline: str
    synopsis: str
    genre: list[str] = Field(default_factory=list)
    tone: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    setting: Setting = Field(default_factory=Setting)
    beats: list[Beat] = Field(default_factory=list)


class PackageShot(StudioModel):
    direction: Shot
    layout: ShotLayout | None = None


class PackageScene(StudioModel):
    id: SceneId
    order: int
    title: str
    beat_ids: list[BeatId]
    location_id: LocationId
    time_of_day: TimeOfDay
    weather: str = ""
    cast: list[CastMember] = Field(default_factory=list)
    prop_ids: list[PropId] = Field(default_factory=list)
    summary: str = ""
    mood: str = ""
    frame_start: int | None = None
    frame_end: int | None = None
    shots: list[PackageShot] = Field(default_factory=list)


class PackageStats(StudioModel):
    characters: int
    locations: int
    props: int
    scenes: int
    shots: int
    dialogue_lines: int
    total_frames: int
    open_build_requests: int


class ProductionPackage(StudioModel):
    package_version: str = "2.0"
    project_id: str
    status: PackageStatus
    created_at: datetime
    input: PackageInput
    story: PackageStory
    style_guide: StyleGuide
    characters: list[Character]
    locations: list[Location]
    props: list[Prop]
    scenes: list[PackageScene]
    build_requests: list[BuildRequest] = Field(default_factory=list)
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    continuity_report: ContinuityReport | None = None
    stats: PackageStats
