"""Agent 6, Continuity Checker: continuity_report.json.

The same Issue model is used by the code hard checks in studio.checks.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from studio.ids import IssueId
from studio.schemas.common import AgentName, StudioModel, VersionedFile

Severity = Literal["error", "warning"]
Category = Literal[
    "schema",
    "missing_reference",
    "missing_asset",
    "beat_coverage",
    "duration",
    "outfit",
    "location",
    "timeline",
    "character_logic",
    "unused_entity",
    "out_of_frame",
    "clip_mismatch",
    "dialogue",
]


class Issue(StudioModel):
    id: IssueId
    severity: Severity
    category: Category
    where: str = ""
    entity_ids: list[str] = Field(default_factory=list)
    message: str
    owner_agent: AgentName
    suggested_fix: str = ""


class ContinuityReport(VersionedFile):
    status: Literal["pass", "fail"]
    issues: list[Issue] = Field(default_factory=list)
    open_build_requests: list[str] = Field(default_factory=list)

    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @classmethod
    def from_issues(cls, issues: list[Issue], open_build_requests: list[str] | None = None) -> "ContinuityReport":
        status = "fail" if any(i.severity == "error" for i in issues) else "pass"
        return cls(status=status, issues=issues, open_build_requests=open_build_requests or [])
