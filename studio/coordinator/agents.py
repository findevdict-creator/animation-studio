"""Agent protocol and registry.

An agent is anything with a name and a ``run`` method that returns a dict
matching its output schema. The coordinator validates it; the agent never
writes files itself. Real agents (Claude calls) live in ``studio.agents``;
``StubAgent`` returns a fixed payload and is used by tests and dry runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from studio.schemas import Config, Manifest, StudioModel
from studio.schemas.continuity import Issue


@dataclass
class AgentContext:
    """Everything an agent may read."""

    config: Config
    manifest: Manifest
    upstream: dict[str, StudioModel] = field(default_factory=dict)
    attempt: int = 1
    feedback: list[Issue] = field(default_factory=list)
    revise_notes: str = ""
    code_report: Any = None  # ContinuityReport from the hard checks, set for the continuity checker

    @property
    def story_text(self) -> str:
        return self.config.story_text

    def get(self, agent: str) -> StudioModel:
        try:
            return self.upstream[agent]
        except KeyError as exc:
            raise KeyError(f"{agent} output is not available upstream") from exc


class Agent(Protocol):
    name: str

    def run(self, ctx: AgentContext) -> dict[str, Any] | StudioModel:  # pragma: no cover - protocol
        ...


class StubAgent:
    """Returns canned payloads: one per attempt, the last repeated."""

    def __init__(self, name: str, *payloads: dict[str, Any] | StudioModel):
        if not payloads:
            raise ValueError("StubAgent needs at least one payload")
        self.name = name
        self.payloads = list(payloads)
        self.calls: list[AgentContext] = []

    def run(self, ctx: AgentContext) -> dict[str, Any] | StudioModel:
        self.calls.append(ctx)
        index = min(len(self.calls), len(self.payloads)) - 1
        return self.payloads[index]


class AgentRegistry:
    def __init__(self, agents: list[Agent] | None = None):
        self._agents: dict[str, Agent] = {}
        for a in agents or []:
            self.register(a)

    def register(self, agent: Agent) -> None:
        self._agents[agent.name] = agent

    def get(self, name: str) -> Agent:
        try:
            return self._agents[name]
        except KeyError as exc:
            raise KeyError(f"no agent registered for '{name}'") from exc

    def __contains__(self, name: str) -> bool:
        return name in self._agents
