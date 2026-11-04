"""LLM agents. Each module exposes one agent class; ``llm_registry`` wires up all six
pipeline agents, each on the provider and model the run config picks for it (see
``studio.llm.settings``). Pass ``clients`` (or ``client`` for the run-wide provider) to
reuse configured SDK clients, or fakes in tests. ``STUDIO_EFFORT`` sets the effort."""

from __future__ import annotations

from typing import Any

from studio.coordinator.agents import AgentRegistry
from studio.llm import LLMSettings

PIPELINE_AGENTS = ("story_analyst", "character_designer", "scene_builder", "director", "layout_td", "continuity_checker")


def build_agent(cls: type, settings: LLMSettings, **kwargs: Any):
    """Instantiate an agent class on the provider and model ``settings`` choose for it."""
    provider, model = settings.for_agent(cls.name)
    return cls(provider=provider, model=model, json_repairs=settings.json_repairs, **kwargs)


def llm_registry(
    config: Any = None,
    settings: LLMSettings | None = None,
    client: Any = None,
    model: str | None = None,
    provider: str | None = None,
) -> AgentRegistry:
    from studio.agents.character_designer import CharacterDesigner
    from studio.agents.continuity_checker import ContinuityChecker
    from studio.agents.director import Director
    from studio.agents.layout_td import LayoutTD
    from studio.agents.scene_builder import SceneBuilder
    from studio.agents.story_analyst import StoryAnalyst

    if settings is None:
        flags = config.flags if config is not None else None
        settings = LLMSettings.from_flags(flags, provider=provider, model=model)
    if client is not None:
        settings.clients.setdefault(settings.run_provider, client)
    registry = AgentRegistry()
    for cls in (StoryAnalyst, CharacterDesigner, SceneBuilder, Director, LayoutTD, ContinuityChecker):
        registry.register(build_agent(cls, settings))
    return registry


def claude_registry(client: Any = None, model: str | None = None) -> AgentRegistry:
    """All six agents on Claude (the registry before module 16)."""
    return llm_registry(client=client, model=model, provider="anthropic")
