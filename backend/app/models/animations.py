"""Animation identity and versions (SRS 30 Phase 6: the animation library).

``animations`` is the stable identity of a reusable animation (ANIM-001): a walk cycle, an
idle, talking gestures, a wave, an expression take. ``animation_versions`` shares its primary
key with ``asset_versions_registry`` like the other asset versions, so status and numbering
follow the same rules and the same publisher writes them. The compatibility record the scene
validator reads is on the version (``compat``, an ``AnimationCompat`` measured by the build):
rig profile, the controls and face properties it keys, frames, loop, travel and stride, the
reference leg length, and the body-fit profiles it is declared for (empty: all of its style).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.assets import AssetVersion
from app.models.base import Base, created_at, uuid_pk


class Animation(Base):
    __tablename__ = "animations"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, comment="ANIM-001")
    name: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    category: Mapped[str] = mapped_column(String(16), comment="locomotion|idle|gesture|talk|expression")
    recommended_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("animation_versions.id", use_alter=True, name="fk_animations_recommended_version_id"))
    lifecycle: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    versions: Mapped[list[AnimationVersion]] = relationship(
        back_populates="animation", foreign_keys="AnimationVersion.animation_id", lazy="selectin",
        order_by="AnimationVersion.id")


class AnimationVersion(Base):
    __tablename__ = "animation_versions"

    id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset_versions_registry.id", ondelete="CASCADE"),
                                          primary_key=True)
    animation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("animations.id"), index=True)
    based_on_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("animation_versions.id"))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spec_digest: Mapped[str] = mapped_column(String(64), comment="ResolvedAnimation digest of spec")
    built_digest: Mapped[str | None] = mapped_column(String(64), comment="digest the current files were built from")
    style_profile_ref: Mapped[str | None] = mapped_column(String(64))
    rig_profile_ref: Mapped[str | None] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(16), comment="generator|bvh")
    generator_ref: Mapped[str | None] = mapped_column(String(64), comment="GEN-WALK@1.0")
    source_upload_key: Mapped[str | None] = mapped_column(Text, comment="BVH take in the work bucket")
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    licence: Mapped[str | None] = mapped_column(Text, comment="provenance of a motion-capture take")
    fit_profiles: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list,
                                                    comment="declared body-fit profiles; empty means all")
    compat: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="AnimationCompat measured by the build")
    check_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="play check on the reference bodies")
    blender_version: Mapped[str | None] = mapped_column(String(32))
    generate_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    review_decision: Mapped[str | None] = mapped_column(String(16), comment="approve|reject")
    review_notes: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manifest_key: Mapped[str | None] = mapped_column(Text)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    animation: Mapped[Animation] = relationship(back_populates="versions", foreign_keys=[animation_id])
    entry: Mapped[AssetVersion] = relationship(lazy="joined", innerjoin=True, foreign_keys=[id])

    @property
    def status(self) -> str:
        return self.entry.status

    @property
    def version_label(self) -> str | None:
        return self.entry.version_label
