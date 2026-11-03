"""Build requests: what stage 1 hands to a modeller or an automated asset route."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from studio.ids import BuildRequestId
from studio.schemas.common import StudioModel
from studio.schemas.manifest import RigType

BuildKind = Literal["character", "environment", "prop", "clip", "garment"]
BuildRoute = Literal["variant", "generator", "asset_builder", "image_to_3d", "human"]
BuildStatus = Literal["open", "in_progress", "review", "accepted", "rejected"]


class BuildAttempt(StudioModel):
    route: BuildRoute
    round: int = Field(ge=1)
    script: str | None = None
    turntable: str | None = None
    qa_score: float | None = Field(default=None, ge=0, le=1)
    qa_notes: str = ""


class BuildDecision(StudioModel):
    by: Literal["qa_agent", "human"]
    at: datetime
    note: str = ""


class BuildRequest(StudioModel):
    id: BuildRequestId
    kind: BuildKind
    for_id: str
    spec: str = Field(min_length=1)
    reference_images: list[str] = Field(default_factory=list)
    nearest_library_id: str | None = None
    # kind-specific
    rig_type: RigType | None = None
    height_m: float | None = Field(default=None, gt=0)
    required_shape_keys: list[str] = Field(default_factory=list)
    required_outfit_slots: list[str] = Field(default_factory=list)
    bounds_m: tuple[float, float, float] | None = None
    required_anchors: list[str] = Field(default_factory=list)
    frames: int | None = Field(default=None, gt=0)
    loop: bool | None = None
    # lifecycle (filled by the coordinator, not by agents)
    screen_time_frames: int = Field(default=0, ge=0)
    route: BuildRoute | None = None
    status: BuildStatus = "open"
    attempts: list[BuildAttempt] = Field(default_factory=list)
    decision: BuildDecision | None = None
    result_library_id: str | None = None
