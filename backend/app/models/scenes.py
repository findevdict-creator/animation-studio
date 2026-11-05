"""Scenes, their immutable versions and the character instances they pin (SRS 16.2, 16.3).

A scene is not a library asset: it is never published and nothing in it ever goes into a
character, outfit or accessory version. ``scene_versions`` are append-only: the manifest
(what the person wrote) and its resolution against the registry (exact version ids and
file SHA-256s) never change after insert. Building ``scene.blend`` and reopening it are
jobs whose results fill the build and verify columns once.

``scene_instances``, ``scene_instance_accessories`` and ``scene_instance_animations`` repeat the pins of each version as
rows with real foreign keys, so "which scenes use CHAR-001@1.0" is a query and a pinned
version can never be deleted from under a scene.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at, uuid_pk

BUILD_STATUSES = ("none", "queued", "built", "failed")
VERIFY_STATUSES = ("none", "queued", "verified", "failed")


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, comment="SCENE-001")
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    lifecycle: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    versions: Mapped[list[SceneVersion]] = relationship(
        back_populates="scene", foreign_keys="SceneVersion.scene_id", lazy="selectin", order_by="SceneVersion.number")


class SceneVersion(Base):
    __tablename__ = "scene_versions"
    __table_args__ = (
        UniqueConstraint("scene_id", "number", name="uq_scene_versions_number"),
        CheckConstraint("number >= 1", name="number"),
        CheckConstraint(f"build_status IN {BUILD_STATUSES}", name="build_status"),
        CheckConstraint(f"verify_status IN {VERIFY_STATUSES}", name="verify_status"),
        CheckConstraint("verify_status = 'none' OR build_status = 'built'", name="verify_needs_build"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenes.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    based_on_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scene_versions.id"))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, comment="SceneManifest as written")
    manifest_digest: Mapped[str] = mapped_column(String(64))
    resolved: Mapped[dict[str, Any]] = mapped_column(JSONB, comment="ResolvedScene: exact version ids and SHA-256s")
    overrides: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, comment="verified-but-unapproved fits a director accepted for this version")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()

    build_status: Mapped[str] = mapped_column(String(16), default="none")
    assemble_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    scene_blend_sha256: Mapped[str | None] = mapped_column(String(64))
    build_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    build_files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list,
                                                               comment="scene.blend, report, frames, thumbnail")
    blender_version: Mapped[str | None] = mapped_column(String(32))
    built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    verify_status: Mapped[str] = mapped_column(String(16), default="none")
    verify_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    verify_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    verify_files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list,
                                                                comment="reopen report, reopened frames")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    scene: Mapped[Scene] = relationship(back_populates="versions", foreign_keys=[scene_id])
    instances: Mapped[list[SceneInstance]] = relationship(
        back_populates="scene_version", lazy="selectin", order_by="SceneInstance.position")


class SceneInstance(Base):
    __tablename__ = "scene_instances"
    __table_args__ = (UniqueConstraint("scene_version_id", "key", name="uq_scene_instances_key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    scene_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scene_versions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(40))
    character_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("character_versions.id"), index=True)
    outfit_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("outfit_versions.id"), index=True)
    outfit_compatibility_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("outfit_compatibility.id"))
    compatibility_status: Mapped[str | None] = mapped_column(String(16), comment="the fit's status when pinned")
    clips: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)

    scene_version: Mapped[SceneVersion] = relationship(back_populates="instances")
    accessories: Mapped[list[SceneInstanceAccessory]] = relationship(
        lazy="selectin", order_by="SceneInstanceAccessory.position")
    animations: Mapped[list[SceneInstanceAnimation]] = relationship(
        lazy="selectin", order_by="SceneInstanceAnimation.position")


class SceneInstanceAccessory(Base):
    __tablename__ = "scene_instance_accessories"
    __table_args__ = (UniqueConstraint("instance_id", "attach_point", name="uq_scene_instance_accessories_point"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scene_instances.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    accessory_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accessory_versions.id"), index=True)
    attach_point: Mapped[str] = mapped_column(String(32))


class SceneInstanceAnimation(Base):
    """A library animation an instance plays (Phase 6): one row per assignment."""

    __tablename__ = "scene_instance_animations"
    __table_args__ = (UniqueConstraint("instance_id", "position", name="uq_scene_instance_animations_position"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scene_instances.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, comment="index in the instance's animations list")
    animation_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("animation_versions.id"), index=True)
    start_frame: Mapped[int] = mapped_column(Integer)
