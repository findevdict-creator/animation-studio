"""LLM agents. Each module exposes one agent class; ``claude_registry`` wires them up."""

from __future__ import annotations

from studio.coordinator.agents import AgentRegistry


def claude_registry() -> AgentRegistry:
    """Agents on the Claude API. Modules are added branch by branch; missing ones raise at run time."""
    registry = AgentRegistry()
    try:
        from studio.agents.story_analyst import StoryAnalyst

        registry.register(StoryAnalyst())
    except ImportError:  # pragma: no cover - until 04-story-analyst lands
        pass
    return registry
