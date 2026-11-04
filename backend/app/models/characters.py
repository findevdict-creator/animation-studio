"""Character identity and versions (architecture 3, 4.1, 6).

``characters`` is the stable identity (code, name, tags, recommended version).
``character_versions`` shares its primary key with ``asset_versions_registry``, which owns
the lifecycle status and the version number, so status and numbering rules live in one
place for every asset kind. Profiles and the template are built-in code in this release,
so a version records them as ``id@version`` references rather than foreign keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.assets import AssetVersion
from app.models.base import Base, created_at, uuid_pk


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, comment="CHAR-001")
    name: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    recommended_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("character_versions.id", use_alter=True, name="fk_characters_recommended_version_id"))
    lifecycle: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    versions: Mapped[list["CharacterVersion"]] = relationship(
        back_populates="character", foreign_keys="CharacterVersion.character_id", lazy="selectin",
        order_by="CharacterVersion.id")


class CharacterVersion(Base):
    __tablename__ = "character_versions"

    id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset_versions_registry.id", ondelete="CASCADE"),
                                          primary_key=True)
    character_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("characters.id"), index=True)
    based_on_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("character_versions.id"))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spec_digest: Mapped[str] = mapped_column(String(64), comment="ResolvedCharacter digest of spec")
    built_digest: Mapped[str | None] = mapped_column(String(64), comment="digest the current files were built from")
    template_ref: Mapped[str | None] = mapped_column(String(64))
    style_profile_ref: Mapped[str | None] = mapped_column(String(64))
    rig_profile_ref: Mapped[str | None] = mapped_column(String(64))
    fit_profile: Mapped[str | None] = mapped_column(String(64))
    height_m: Mapped[float | None] = mapped_column(Float)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
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

    character: Mapped[Character] = relationship(back_populates="versions", foreign_keys=[character_id])
    entry: Mapped[AssetVersion] = relationship(lazy="joined", innerjoin=True, foreign_keys=[id])

    @property
    def status(self) -> str:
        return self.entry.status

    @property
    def version_label(self) -> str | None:
        return self.entry.version_label
