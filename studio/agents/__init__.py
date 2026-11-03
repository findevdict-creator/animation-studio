"""LLM agents on the Claude API. Each module exposes one agent class; ``claude_registry``
wires up all six. Pass a client to reuse a configured ``anthropic.Anthropic`` (or a fake
in tests); ``STUDIO_MODEL`` and ``STUDIO_EFFORT`` override the defaults."""

from __future__ import annotations

import anthropic

from studio.coordinator.agents import AgentRegistry


def claude_registry(client: anthropic.Anthropic | None = None, model: str | None = None) -> AgentRegistry:
    from studio.agents.character_designer import CharacterDesigner
    from studio.agents.continuity_checker import ContinuityChecker
    from studio.agents.director import Director
    from studio.agents.layout_td import LayoutTD
    from studio.agents.scene_builder import SceneBuilder
    from studio.agents.story_analyst import StoryAnalyst

    kwargs = {"client": client}
    if model:
        kwargs["model"] = model
    registry = AgentRegistry()
    registry.register(StoryAnalyst(**kwargs))
    registry.register(CharacterDesigner(**kwargs))
    registry.register(SceneBuilder(**kwargs))
    registry.register(Director(**kwargs))
    registry.register(LayoutTD(**kwargs))
    registry.register(ContinuityChecker(**kwargs))
    return registry
