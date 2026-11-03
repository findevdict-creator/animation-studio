"""Stub agents that replay the fixture story, for dry runs of the coordinator."""

from __future__ import annotations

import json
from pathlib import Path

from studio.coordinator.agents import AgentRegistry, StubAgent
from studio.schemas import AGENT_FILES

FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "lantern_fox"


def stub_registry(fixture_dir: Path = FIXTURE_DIR) -> AgentRegistry:
    registry = AgentRegistry()
    for agent, filename in AGENT_FILES.items():
        path = fixture_dir / filename
        if path.exists():
            registry.register(StubAgent(agent, json.loads(path.read_text())))
        elif agent == "continuity_checker":
            registry.register(StubAgent(agent, {"status": "pass", "issues": []}))
    return registry
