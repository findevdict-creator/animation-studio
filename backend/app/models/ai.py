"""AI generation log (SRS 24: "maintain logs of AI-generated specifications and provider
metadata"). Append-only: a trigger refuses UPDATE and DELETE."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk


class AIGeneration(Base):
    __tablename__ = "ai_generations"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    task: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    prompt_sha256: Mapped[str] = mapped_column(String(64), comment="system + user prompt, images excluded")
    input_refs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, comment="params, image keys and hashes")
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="the validated output, or the last bad one")
    issues: Mapped[list[Any] | None] = mapped_column(JSONB)
    valid: Mapped[bool] = mapped_column(Boolean)
    stop: Mapped[str] = mapped_column(String(16), comment="ok|refusal|max_tokens|error")
    repair_turns: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=1)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
