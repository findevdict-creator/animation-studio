"""Accessory identity and versions (SRS 8, architecture 5.3).

``accessories`` is the stable identity of a reusable accessory (ACC-001): glasses, a hat, a
watch, a bag, a toy. ``accessory_versions`` shares its primary key with
``asset_versions_registry`` like character and outfit versions, so status and numbering
follow the same rules and the same publisher writes them. The compatibility metadata the
scene validator reads is on the version: rig profile, the attachment points the item may
go on, and the body-fit profiles it is declared for (empty: every profile of its style).
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


class Accessory(Base):
    __tablename__ = "accessories"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, comment="ACC-001")
    name: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    category: Mapped[str] = mapped_column(String(16), comment="eyewear|headwear|wristwear|bag|toy")
    recommended_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accessory_versions.id", use_alter=True, name="fk_accessories_recommended_version_id"))
    lifecycle: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    versions: Mapped[list[AccessoryVersion]] = relationship(
        back_populates="accessory", foreign_keys="AccessoryVersion.accessory_id", lazy="selectin",
        order_by="AccessoryVersion.id")


class AccessoryVersion(Base):
    __tablename__ = "accessory_versions"

    id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset_versions_registry.id", ondelete="CASCADE"),
                                          primary_key=True)
    accessory_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accessories.id"), index=True)
    based_on_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accessory_versions.id"))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spec_digest: Mapped[str] = mapped_column(String(64), comment="ResolvedAccessory digest of spec")
    built_digest: Mapped[str | None] = mapped_column(String(64), comment="digest the current files were built from")
    style_profile_ref: Mapped[str | None] = mapped_column(String(64))
    rig_profile_ref: Mapped[str | None] = mapped_column(String(64))
    catalog_ref: Mapped[str | None] = mapped_column(String(64))
    item_ref: Mapped[str | None] = mapped_column(String(64), comment="ACCD-GLASSES@0.1")
    attach_point: Mapped[str | None] = mapped_column(String(32), comment="default attachment point")
    attach_points: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    fit_profiles: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list,
                                                    comment="declared body-fit profiles; empty means all")
    conflicts_with_slots: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list)
    check_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="attach check on the reference body")
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

    accessory: Mapped[Accessory] = relationship(back_populates="versions", foreign_keys=[accessory_id])
    entry: Mapped[AssetVersion] = relationship(lazy="joined", innerjoin=True, foreign_keys=[id])

    @property
    def status(self) -> str:
        return self.entry.status

    @property
    def version_label(self) -> str | None:
        return self.entry.version_label
