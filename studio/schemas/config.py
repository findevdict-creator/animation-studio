"""Pipeline input: config, feature flags and approval settings."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from studio.schemas.common import AgentName, StudioModel


class CharacterReference(StudioModel):
    character_id_hint: str
    image_path: str
    source: Literal["generative", "hand_drawn", "photo"] = "generative"


class HumanApproval(StudioModel):
    mode: Literal["all", "none", "selected"] = "all"
    agents: list[AgentName] = Field(default_factory=list)
    timeout_sec: float | None = None

    def gate_on(self, agent: str) -> bool:
        if self.mode == "all":
            return True
        if self.mode == "none":
            return False
        return agent in self.agents


class Flags(StudioModel):
    render_backend: Literal["blender", "none"] = "blender"
    library_mode: Literal["strict", "build_requests", "freeform"] = "build_requests"
    generative_references: Literal["off", "input_only", "generate_missing"] = "input_only"
    layout_td: bool = True
    continuity_checker: bool = True
    continuity_fix_rounds: int = Field(default=2, ge=0)
    agent_retries: int = Field(default=2, ge=0)
    parallel_calls: bool = True
    human_exports: list[Literal["screenplay", "shot_list", "bible"]] = Field(
        default_factory=lambda: ["screenplay", "shot_list", "bible"]
    )
    strict_dialogue: bool = True
    duration_tolerance: float = Field(default=0.10, ge=0, le=1)
    # Asset build flags (Asset Build Implementation Plan)
    character_mode: Literal["parametric", "library_only", "build_request"] = "parametric"
    environment_mode: Literal["composed", "library_only", "build_request"] = "composed"
    clip_sources: list[Literal["baked", "procedural"]] = Field(
        default_factory=lambda: ["baked", "procedural"]
    )
    auto_routes: list[Literal["variant", "generator", "asset_builder", "image_to_3d"]] = Field(
        default_factory=lambda: ["variant", "generator", "asset_builder"]
    )
    asset_builder_rounds: int = Field(default=3, ge=0)
    qa_accept_score: float = Field(default=0.8, ge=0, le=1)

    @model_validator(mode="after")
    def _consistent(self) -> "Flags":
        if self.render_backend == "blender" and not self.layout_td:
            raise ValueError("layout_td must be true when render_backend is 'blender'")
        if self.library_mode == "freeform" and self.render_backend == "blender":
            raise ValueError("library_mode 'freeform' cannot feed the blender backend")
        return self


class Config(StudioModel):
    story_text: str = Field(min_length=1)
    target_duration_sec: float = Field(gt=0)
    visual_style: str = "stylised 3D, toon shading, storybook palette"
    aspect_ratio: str = "16:9"
    resolution: tuple[int, int] = (1920, 1080)
    fps: int = Field(default=24, gt=0)
    audience: str = "general"
    language: str = "en"
    library_manifest_path: str = "library/manifest.json"
    character_references: list[CharacterReference] = Field(default_factory=list)
    human_approval: HumanApproval = Field(default_factory=HumanApproval)
    flags: Flags = Field(default_factory=Flags)

    @property
    def target_frames(self) -> int:
        return round(self.target_duration_sec * self.fps)

    def agents_enabled(self) -> list[str]:
        agents = ["story_analyst", "character_designer", "scene_builder", "director"]
        if self.flags.layout_td and self.flags.render_backend == "blender":
            agents.append("layout_td")
        if self.flags.continuity_checker:
            agents.append("continuity_checker")
        return agents
