"""Outfit identity, versions and the compatibility registry (SRS 7, architecture 4.1, 6).

``outfits`` is the stable identity of a reusable outfit asset (OUTFIT-001). ``outfit_versions``
shares its primary key with ``asset_versions_registry`` exactly like character versions, so
status and numbering follow the same rules. An outfit never holds a character: its scope
says who it is for (one character, a body-fit profile, or several profiles), and which
character versions it actually fits is recorded in ``outfit_compatibility``, one row per
(outfit version, character version) pair, with the fit report that is its evidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.assets import AssetVersion
from app.models.base import Base, created_at, uuid_pk

SCOPES = ("character", "body_profile", "multi")
#: candidate: queued for a fit check; verified / failed: the check's verdict; approved: a
#: reviewer accepted a verified fit for production; revoked: withdrawn (by a person, or
#: because a newer check on the same files failed)
COMPAT_STATUSES = ("candidate", "verified", "failed", "approved", "revoked")


class Outfit(Base):
    __tablename__ = "outfits"
    __table_args__ = (
        CheckConstraint(f"scope IN {SCOPES}", name="scope"),
        CheckConstraint("(scope = 'character') = (owner_character_id IS NOT NULL)", name="owner_for_character_scope"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, comment="OUTFIT-001")
    name: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    scope: Mapped[str] = mapped_column(String(16))
    owner_character_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("characters.id"), index=True)
    recommended_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("outfit_versions.id", use_alter=True, name="fk_outfits_recommended_version_id"))
    lifecycle: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    versions: Mapped[list["OutfitVersion"]] = relationship(
        back_populates="outfit", foreign_keys="OutfitVersion.outfit_id", lazy="selectin", order_by="OutfitVersion.id")


class OutfitVersion(Base):
    __tablename__ = "outfit_versions"

    id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset_versions_registry.id", ondelete="CASCADE"),
                                          primary_key=True)
    outfit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outfits.id"), index=True)
    based_on_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("outfit_versions.id"))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spec_digest: Mapped[str] = mapped_column(String(64), comment="ResolvedOutfit digest of spec")
    built_digest: Mapped[str | None] = mapped_column(String(64), comment="digest the current files were built from")
    style_profile_ref: Mapped[str | None] = mapped_column(String(64))
    rig_profile_ref: Mapped[str | None] = mapped_column(String(64))
    catalog_ref: Mapped[str | None] = mapped_column(String(64))
    fit_profiles: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    primary_fit_profile: Mapped[str | None] = mapped_column(String(64))
    slots: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list)
    fit_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="fit check on the reference body")
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

    outfit: Mapped[Outfit] = relationship(back_populates="versions", foreign_keys=[outfit_id])
    entry: Mapped[AssetVersion] = relationship(lazy="joined", innerjoin=True, foreign_keys=[id])

    @property
    def status(self) -> str:
        return self.entry.status

    @property
    def version_label(self) -> str | None:
        return self.entry.version_label


class OutfitCompatibility(Base):
    """One outfit version on one character version: the fit check's verdict and evidence.

    The report and its files describe exact bytes (both blend SHA-256s are recorded), so a
    row stays true for as long as neither version's files change; published versions never
    change, and a draft's rows are dropped whenever its build is replaced."""

    __tablename__ = "outfit_compatibility"
    __table_args__ = (
        UniqueConstraint("outfit_version_id", "character_version_id", name="uq_outfit_compatibility_pair"),
        CheckConstraint(f"status IN {COMPAT_STATUSES}", name="status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    outfit_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outfit_versions.id", ondelete="CASCADE"),
                                                         index=True)
    character_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("character_versions.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    fit_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    fit_profile: Mapped[str | None] = mapped_column(String(64), comment="the wearer's body-fit profile")
    fit_profile_declared: Mapped[bool | None] = mapped_column(comment="the outfit declares the wearer's profile")
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list,
                                                         comment="report, thumbnail, pose sheet, try-on GLB")
    outfit_blend_sha256: Mapped[str | None] = mapped_column(String(64))
    character_blend_sha256: Mapped[str | None] = mapped_column(String(64))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
