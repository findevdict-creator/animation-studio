"""Series, episodes, scripts, storyboards, episode versions and render jobs (SRS 13 to 18, 20;
architecture 7.3).

* ``series`` hold a style profile and render settings; ``series_members`` cast characters by
  identity (never a copy of their geometry, SRS 13) with a default version, outfit and voice;
  ``series_assets`` is the series' asset collection of exact versions.
* ``episodes`` belong to a series. ``script_versions`` and ``storyboards`` (with their
  ``shots``) are append-only: an edit is a new number. AI output lands there as a version
  like any other, with its ``ai_generations`` row, and is never used until a person picks it.
* ``episode_scenes`` maps each shot of an episode to the scene identity its versions live in.
* ``episode_versions`` freeze what an episode is made of: the manifest (cast, shots, audio,
  render settings), one scene version per shot (``episode_version_shots``), the closure of
  every library version it depends on (``episode_assets``) and every audio clip
  (``episode_audio``). Once ``locked`` none of that changes or goes away.
* ``render_jobs`` record a render of a locked episode version (SRS 18.2): its settings, the
  asset versions it used, a ``render_scene`` job per shot (``render_job_shots``), the
  ``export_episode`` job, and the ``render_outputs`` (PNG sequences, the MP4, reports).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at, uuid_pk

MEMBER_ROLES = ("main", "supporting")
SCRIPT_SOURCES = ("manual", "ai")
STORYBOARD_SOURCES = ("rule", "ai", "manual", "voiced")
EPISODE_VERSION_STATUSES = ("draft", "locked")
RENDER_STATUSES = ("queued", "rendering", "exporting", "completed", "failed", "cancelled")


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Series(Base):
    __tablename__ = "series"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, comment="SERIES-001")
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    style_profile_ref: Mapped[str] = mapped_column(String(64), comment="family_cartoon@1.0")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, comment="SeriesSettings")
    lifecycle: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = _updated()

    members: Mapped[list[SeriesMember]] = relationship(lazy="selectin", order_by="SeriesMember.position")
    assets: Mapped[list[SeriesAsset]] = relationship(lazy="selectin", order_by="SeriesAsset.created_at")


class SeriesMember(Base):
    __tablename__ = "series_members"
    __table_args__ = (UniqueConstraint("series_id", "character_id", name="uq_series_members_character"),
                      UniqueConstraint("series_id", "key", name="uq_series_members_key"),
                      CheckConstraint(f"role IN {MEMBER_ROLES}", name="role"))

    id: Mapped[uuid.UUID] = uuid_pk()
    series_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("series.id"), index=True)
    character_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("characters.id"), index=True)
    key: Mapped[str] = mapped_column(String(32), comment="instance key in the series' scenes, e.g. ali")
    role: Mapped[str] = mapped_column(String(16), default="main")
    position: Mapped[int] = mapped_column(Integer, default=0)
    default_character_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("character_versions.id"))
    default_outfit_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("outfit_versions.id"))
    voice: Mapped[dict[str, Any]] = mapped_column(JSONB, comment="VoiceProfile: the same voice in every episode")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = _updated()


class SeriesAsset(Base):
    """The series' asset collection: exact library versions (environments, props, …)."""

    __tablename__ = "series_assets"
    __table_args__ = (UniqueConstraint("series_id", "asset_version_id", name="uq_series_assets_version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    series_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("series.id"), index=True)
    asset_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset_versions_registry.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = created_at()


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("series_id", "number", name="uq_episodes_number"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, comment="EP-001")
    series_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("series.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(120))
    concept: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = _updated()


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[uuid.UUID] = uuid_pk()
    episode_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("episodes.id"), unique=True)
    created_at: Mapped[datetime] = created_at()


class ScriptVersion(Base):
    __tablename__ = "script_versions"
    __table_args__ = (UniqueConstraint("script_id", "number", name="uq_script_versions_number"),
                      CheckConstraint(f"source IN {SCRIPT_SOURCES}", name="source"),
                      CheckConstraint("source <> 'ai' OR ai_generation_id IS NOT NULL", name="ai_logged"))

    id: Mapped[uuid.UUID] = uuid_pk()
    script_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scripts.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, comment="ScriptSpecification")
    digest: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))
    ai_generation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_generations.id"))
    based_on_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("script_versions.id"))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()


class Storyboard(Base):
    __tablename__ = "storyboards"
    __table_args__ = (UniqueConstraint("episode_id", "number", name="uq_storyboards_number"),
                      CheckConstraint(f"source IN {STORYBOARD_SOURCES}", name="source"),
                      CheckConstraint("source <> 'ai' OR ai_generation_id IS NOT NULL", name="ai_logged"))

    id: Mapped[uuid.UUID] = uuid_pk()
    episode_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("episodes.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    script_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("script_versions.id"), index=True)
    source: Mapped[str] = mapped_column(String(16))
    ai_generation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_generations.id"))
    based_on_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("storyboards.id"))
    digest: Mapped[str] = mapped_column(String(64))
    duration_s: Mapped[float] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()

    shots: Mapped[list[ShotRow]] = relationship(lazy="selectin", order_by="ShotRow.position")


class ShotRow(Base):
    __tablename__ = "shots"
    __table_args__ = (UniqueConstraint("storyboard_id", "shot_id", name="uq_shots_shot_id"),
                      UniqueConstraint("storyboard_id", "position", name="uq_shots_position"))

    id: Mapped[uuid.UUID] = uuid_pk()
    storyboard_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("storyboards.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    shot_id: Mapped[str] = mapped_column(String(16), comment="SH-001")
    scene_id: Mapped[str] = mapped_column(String(16), comment="the script scene, SC-001")
    duration_s: Mapped[float] = mapped_column(Float)
    camera_type: Mapped[str] = mapped_column(String(16))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, comment="Shot")


class EpisodeScene(Base):
    """The scene identity a shot's versions live in, for every version of the episode."""

    __tablename__ = "episode_scenes"
    __table_args__ = (UniqueConstraint("episode_id", "shot_id", name="uq_episode_scenes_shot"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    episode_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("episodes.id"), index=True)
    shot_id: Mapped[str] = mapped_column(String(16))
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenes.id"), unique=True)


class EpisodeVersion(Base):
    __tablename__ = "episode_versions"
    __table_args__ = (UniqueConstraint("episode_id", "number", name="uq_episode_versions_number"),
                      CheckConstraint(f"status IN {EPISODE_VERSION_STATUSES}", name="status"),
                      CheckConstraint("status <> 'locked' OR locked_at IS NOT NULL", name="locked_at"))

    id: Mapped[uuid.UUID] = uuid_pk()
    episode_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("episodes.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, comment="EpisodeManifest")
    digest: Mapped[str] = mapped_column(String(64))
    script_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("script_versions.id"))
    storyboard_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("storyboards.id"))
    duration_s: Mapped[float] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    shots: Mapped[list[EpisodeVersionShot]] = relationship(lazy="selectin", order_by="EpisodeVersionShot.position")


class EpisodeVersionShot(Base):
    __tablename__ = "episode_version_shots"
    __table_args__ = (UniqueConstraint("episode_version_id", "position", name="uq_episode_version_shots_position"),
                      UniqueConstraint("episode_version_id", "shot_id", name="uq_episode_version_shots_shot"))

    id: Mapped[uuid.UUID] = uuid_pk()
    episode_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("episode_versions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    shot_id: Mapped[str] = mapped_column(String(16))
    scene_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scene_versions.id"), index=True)
    start_s: Mapped[float] = mapped_column(Float, comment="where the shot starts on the episode timeline")
    duration_s: Mapped[float] = mapped_column(Float)
    transition: Mapped[dict[str, Any]] = mapped_column(JSONB, comment="Transition in")


class EpisodeAsset(Base):
    """The closure of library versions an episode version depends on (architecture 7.3)."""

    __tablename__ = "episode_assets"

    episode_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("episode_versions.id"), primary_key=True)
    asset_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset_versions_registry.id"), primary_key=True,
                                                        index=True)
    kind: Mapped[str] = mapped_column(String(32))
    display: Mapped[str] = mapped_column(String(48), comment="CHAR-001@1.0")


class EpisodeAudio(Base):
    __tablename__ = "episode_audio"

    episode_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("episode_versions.id"), primary_key=True)
    audio_clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audio_clips.id"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(16), primary_key=True, comment="dialogue | music | sfx")


class RenderJob(Base):
    __tablename__ = "render_jobs"
    __table_args__ = (CheckConstraint(f"status IN {RENDER_STATUSES}", name="status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, comment="RENDER-001")
    episode_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("episodes.id"), index=True)
    episode_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("episode_versions.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, comment="RenderSettings in force")
    asset_versions: Mapped[list[str]] = mapped_column(JSONB, comment="the closure, CHAR-001@1.0 …, as rendered")
    blender_version: Mapped[str | None] = mapped_column(String(32))
    export_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    output_location: Mapped[str | None] = mapped_column(Text, comment="work bucket key of the MP4")
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _updated()

    shots: Mapped[list[RenderJobShot]] = relationship(lazy="selectin", order_by="RenderJobShot.position")
    outputs: Mapped[list[RenderOutput]] = relationship(lazy="selectin", order_by="RenderOutput.created_at")


class RenderJobShot(Base):
    __tablename__ = "render_job_shots"
    __table_args__ = (UniqueConstraint("render_job_id", "position", name="uq_render_job_shots_position"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    render_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("render_jobs.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    shot_id: Mapped[str] = mapped_column(String(16))
    scene_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scene_versions.id"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    frames: Mapped[int | None] = mapped_column(Integer)


class RenderOutput(Base):
    __tablename__ = "render_outputs"

    id: Mapped[uuid.UUID] = uuid_pk()
    render_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("render_jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), comment="frames | video | audio | report")
    scene_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scene_versions.id"))
    shot_id: Mapped[str | None] = mapped_column(String(16))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    storage_key: Mapped[str] = mapped_column(Text, comment="the object, or the frames' common prefix")
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    frame_count: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, comment="frames with SHA-256, ffprobe, …")
    created_at: Mapped[datetime] = created_at()
