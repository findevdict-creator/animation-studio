"""Base model and shared enums."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "2.0"


class StudioModel(BaseModel):
    """All pipeline models forbid unknown fields so agent drift is caught early."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VersionedFile(StudioModel):
    schema_version: str = Field(default=SCHEMA_VERSION)


TimeOfDay = Literal["dawn", "day", "dusk", "night"]
SettingType = Literal["interior", "exterior"]
Role = Literal["protagonist", "antagonist", "supporting", "background"]
AssetStatus = Literal["library", "parametric", "composed", "build_request"]
AgentName = Literal[
    "story_analyst",
    "character_designer",
    "scene_builder",
    "director",
    "layout_td",
    "continuity_checker",
]
AGENT_ORDER: tuple[str, ...] = (
    "story_analyst",
    "character_designer",
    "scene_builder",
    "director",
    "layout_td",
    "continuity_checker",
)
