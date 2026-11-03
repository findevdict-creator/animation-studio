"""LLM agents on the Claude API. Each module exposes one agent class; ``claude_registry`` wires
them up. Agents not yet implemented fall back to nothing: the coordinator raises a clear
KeyError when it reaches one, so a partial registry still runs the agents that exist."""

from __future__ import annotations

import anthropic

from studio.coordinator.agents import AgentRegistry


def claude_registry(client: anthropic.Anthropic | None = None, model: str | None = None) -> AgentRegistry:
    from studio.agents.character_designer import CharacterDesigner
    from studio.agents.scene_builder import SceneBuilder
    from studio.agents.story_analyst import StoryAnalyst

    kwargs = {"client": client}
    if model:
        kwargs["model"] = model
    registry = AgentRegistry()
    registry.register(StoryAnalyst(**kwargs))
    registry.register(CharacterDesigner(**kwargs))
    registry.register(SceneBuilder(**kwargs))
    return registry
