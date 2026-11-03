"""Pydantic models for every file in the pipeline."""

from studio.schemas.approvals import ApprovalRecord, Approvals
from studio.schemas.build_requests import BuildRequest
from studio.schemas.characters import Character, Characters
from studio.schemas.common import AGENT_ORDER, SCHEMA_VERSION, StudioModel
from studio.schemas.config import Config, Flags, HumanApproval
from studio.schemas.continuity import ContinuityReport, Issue
from studio.schemas.direction import Direction, Shot
from studio.schemas.layout import Layout, ShotLayout
from studio.schemas.manifest import Manifest
from studio.schemas.package import ProductionPackage
from studio.schemas.story_bible import StoryBible
from studio.schemas.world import World

#: Output model per agent, in pipeline order.
AGENT_OUTPUTS: dict[str, type[StudioModel]] = {
    "story_analyst": StoryBible,
    "character_designer": Characters,
    "scene_builder": World,
    "director": Direction,
    "layout_td": Layout,
    "continuity_checker": ContinuityReport,
}

#: File name each agent writes.
AGENT_FILES: dict[str, str] = {
    "story_analyst": "story_bible.json",
    "character_designer": "characters.json",
    "scene_builder": "world.json",
    "director": "direction.json",
    "layout_td": "layout.json",
    "continuity_checker": "continuity_report.json",
}

__all__ = [
    "AGENT_FILES",
    "AGENT_ORDER",
    "AGENT_OUTPUTS",
    "SCHEMA_VERSION",
    "ApprovalRecord",
    "Approvals",
    "BuildRequest",
    "Character",
    "Characters",
    "Config",
    "ContinuityReport",
    "Direction",
    "Flags",
    "HumanApproval",
    "Issue",
    "Layout",
    "Manifest",
    "ProductionPackage",
    "Shot",
    "ShotLayout",
    "StoryBible",
    "StudioModel",
    "World",
]
