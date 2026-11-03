"""The coordinator: plain code that runs agents in order, validates, retries,
pauses for approval, routes continuity errors and merges the package."""

from studio.coordinator.agents import Agent, AgentContext, AgentRegistry, StubAgent
from studio.coordinator.gates import ApprovalGate, AutoApproveGate, AwaitingApproval, FileGate, GateDecision
from studio.coordinator.pipeline import Coordinator, PipelineError
from studio.coordinator.store import RunStore

__all__ = [
    "Agent",
    "AgentContext",
    "AgentRegistry",
    "ApprovalGate",
    "AutoApproveGate",
    "AwaitingApproval",
    "Coordinator",
    "FileGate",
    "GateDecision",
    "PipelineError",
    "RunStore",
    "StubAgent",
]
