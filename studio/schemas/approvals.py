"""Approval gate records: approvals.json"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from studio.schemas.common import AgentName, StudioModel

Decision = Literal["approve", "edit", "revise", "auto"]


class ApprovalRecord(StudioModel):
    agent: AgentName
    attempt: int = Field(ge=1)
    decision: Decision
    notes: str = ""
    decided_by: str = "cli"
    decided_at: datetime
    edited_payload: dict[str, Any] | None = None


class Approvals(StudioModel):
    records: list[ApprovalRecord] = Field(default_factory=list)
