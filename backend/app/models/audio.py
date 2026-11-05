"""Dialogue clips for lip sync (SRS 30 Phase 6, architecture R10).

An ``audio_clips`` row (AUDIO-001) is one uploaded recording, normalised by the media worker
to 16-bit mono WAV, with the mouth cues the analyser found in it. A scene's dialogue line
names the clip by code; the scene resolution freezes its WAV's SHA-256 and its cues, so the
face track of a scene version never changes after the fact.

A clip is written twice: created ``analysing`` when the lipsync job is submitted, then
finished once by that job (``ready`` with the WAV and cues, or ``failed`` with the reason).
After that it never changes, and a ready clip is never deleted (migration 0010).

Phase 8 adds music and sound effects (``kind``; no mouth cues) and dialogue spoken by a
speech provider (``source = 'tts'``): such a clip has no upload, and keeps the exact text,
voice profile and provider it was synthesised with (SRS 17), so it can be explained later.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk

AUDIO_STATUSES = ("analysing", "ready", "failed")
AUDIO_KINDS = ("dialogue", "music", "sfx")
AUDIO_SOURCES = ("upload", "tts")


class AudioClip(Base):
    __tablename__ = "audio_clips"
    __table_args__ = (
        CheckConstraint(f"status IN {AUDIO_STATUSES}", name="status"),
        CheckConstraint("status <> 'ready' OR (wav_storage_key IS NOT NULL AND wav_sha256 IS NOT NULL "
                        "AND duration_s IS NOT NULL AND (kind <> 'dialogue' OR cues IS NOT NULL))",
                        name="ready_has_result"),
        CheckConstraint(f"kind IN {AUDIO_KINDS}", name="kind"),
        CheckConstraint(f"source IN {AUDIO_SOURCES}", name="source"),
        CheckConstraint("(source = 'upload' AND upload_id IS NOT NULL) OR "
                        "(source = 'tts' AND upload_id IS NULL AND voice IS NOT NULL AND dialog_text IS NOT NULL "
                        "AND kind = 'dialogue')", name="source_input"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, comment="AUDIO-001")
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16), default="dialogue", server_default="dialogue")
    source: Mapped[str] = mapped_column(String(16), default="upload", server_default="upload")
    upload_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("uploads.id"), index=True)
    voice: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="VoiceProfile a TTS clip was spoken with")
    provider: Mapped[str | None] = mapped_column(String(64), comment="the speech provider and its version, e.g. espeak-ng-1.51")
    dialog_text: Mapped[str | None] = mapped_column(Text, comment="transcript given to the analyser")
    status: Mapped[str] = mapped_column(String(16), default="analysing")
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    wav_storage_key: Mapped[str | None] = mapped_column(Text, comment="work bucket: the normalised dialogue.wav")
    wav_sha256: Mapped[str | None] = mapped_column(String(64))
    duration_s: Mapped[float | None] = mapped_column(Float)
    analyser: Mapped[str | None] = mapped_column(String(64), comment="rhubarb-1.14.0 or studio-phonetic-1.0")
    recognizer: Mapped[str | None] = mapped_column(String(32), comment="pocketSphinx | phonetic")
    cues: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, comment="MouthCue list")
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
