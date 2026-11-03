"""Human approval gates.

After an agent's output passes the hard checks, the coordinator asks the
gate. A gate answers ``approve``, ``edit`` (with a corrected payload) or
``revise`` (with notes; the agent reruns). ``FileGate`` is the production
gate: it writes the output under ``review/`` and stops the run; a person
answers with ``studio approve`` and the run resumes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class GateDecision:
    decision: str  # approve | edit | revise | auto
    notes: str = ""
    edited_payload: dict[str, Any] | None = None
    decided_by: str = "cli"


class AwaitingApproval(Exception):
    """Raised to stop the run until a person answers the gate."""

    def __init__(self, agent: str, review_path: Path):
        super().__init__(f"{agent}: awaiting approval, see {review_path}")
        self.agent = agent
        self.review_path = review_path


class ApprovalGate(Protocol):
    def decide(self, agent: str, attempt: int, payload: dict[str, Any]) -> GateDecision:  # pragma: no cover
        ...


class AutoApproveGate:
    """Approves everything; used when human_approval.mode is 'none' or in tests."""

    def decide(self, agent: str, attempt: int, payload: dict[str, Any]) -> GateDecision:
        return GateDecision(decision="auto", decided_by="auto")


@dataclass
class ScriptedGate:
    """Test gate: answers from a queue per agent, auto-approves when the queue is empty."""

    answers: dict[str, list[GateDecision]] = field(default_factory=dict)
    seen: list[tuple[str, int]] = field(default_factory=list)

    def decide(self, agent: str, attempt: int, payload: dict[str, Any]) -> GateDecision:
        self.seen.append((agent, attempt))
        queue = self.answers.get(agent) or []
        if queue:
            return queue.pop(0)
        return GateDecision(decision="approve", decided_by="scripted")


class FileGate:
    """Writes ``review/<agent>.json`` and stops; resumes from ``review/<agent>.decision.json``."""

    def __init__(self, review_dir: Path):
        self.review_dir = Path(review_dir)
        self.review_dir.mkdir(parents=True, exist_ok=True)

    def review_path(self, agent: str) -> Path:
        return self.review_dir / f"{agent}.json"

    def decision_path(self, agent: str) -> Path:
        return self.review_dir / f"{agent}.decision.json"

    def decide(self, agent: str, attempt: int, payload: dict[str, Any]) -> GateDecision:
        dpath = self.decision_path(agent)
        if dpath.exists():
            data = json.loads(dpath.read_text())
            dpath.unlink()
            self.review_path(agent).unlink(missing_ok=True)
            return GateDecision(
                decision=data["decision"],
                notes=data.get("notes", ""),
                edited_payload=data.get("edited_payload"),
                decided_by=data.get("decided_by", "cli"),
            )
        self.review_path(agent).write_text(json.dumps(payload, indent=2))
        raise AwaitingApproval(agent, self.review_path(agent))

    @staticmethod
    def write_decision(
        review_dir: Path,
        agent: str,
        decision: str,
        notes: str = "",
        edited_payload: dict[str, Any] | None = None,
        decided_by: str = "cli",
    ) -> Path:
        if decision not in ("approve", "edit", "revise"):
            raise ValueError("decision must be approve, edit or revise")
        if decision == "edit" and edited_payload is None:
            raise ValueError("edit needs an edited payload")
        path = Path(review_dir) / f"{agent}.decision.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "decision": decision,
                    "notes": notes,
                    "edited_payload": edited_payload,
                    "decided_by": decided_by,
                },
                indent=2,
            )
        )
        return path
