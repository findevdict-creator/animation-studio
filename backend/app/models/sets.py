"""Props and environments: identity and versions (SRS 12, architecture 5.3).

``props`` (PROP-001) are reusable objects a scene places; ``environments`` (ENV-001) are sets
a scene happens in. Each version shares its primary key with ``asset_versions_registry``
like every other asset version, so status and numbering follow the same rules and the same
publisher writes them. What a scene needs without opening the files is on the version: a
prop's size, whether it is solid and where things go on it; an environment's size,
lighting and layout (footprints of its solid dressing, tops, seats, marks, camera marks).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from app.models.assets import AssetVersion
from app.models.base import Base, created_at, uuid_pk


class _Identity:
    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    lifecycle: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    @declared_attr
    def created_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(ForeignKey("users.id"), index=True)


class _Version:
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spec_digest: Mapped[str] = mapped_column(String(64), comment="resolved digest of spec")
    built_digest: Mapped[str | None] = mapped_column(String(64), comment="digest the current files were built from")
    style_profile_ref: Mapped[str | None] = mapped_column(String(64))
    catalog_ref: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[list[float]] = mapped_column(ARRAY(Float), default=list, comment="width, depth, height in metres")
    check_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="measured check of the linked file")
    blender_version: Mapped[str | None] = mapped_column(String(32))
    review_decision: Mapped[str | None] = mapped_column(String(16), comment="approve|reject")
    review_notes: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manifest_key: Mapped[str | None] = mapped_column(Text)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    @declared_attr
    def id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(ForeignKey("asset_versions_registry.id", ondelete="CASCADE"), primary_key=True)

    @declared_attr
    def generate_job_id(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(ForeignKey("jobs.id"))

    @declared_attr
    def reviewed_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(ForeignKey("users.id"))

    @declared_attr
    def published_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(ForeignKey("users.id"))

    @declared_attr
    def entry(cls) -> Mapped[AssetVersion]:
        return relationship(lazy="joined", innerjoin=True, foreign_keys=[cls.id])

    @property
    def status(self) -> str:
        return self.entry.status

    @property
    def version_label(self) -> str | None:
        return self.entry.version_label


class Prop(_Identity, Base):
    __tablename__ = "props"

    category: Mapped[str] = mapped_column(String(16), comment="furniture|household|toy|nature|vehicle|food")
    recommended_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prop_versions.id", use_alter=True, name="fk_props_recommended_version_id"))

    versions: Mapped[list[PropVersion]] = relationship(
        back_populates="owner", foreign_keys="PropVersion.prop_id", lazy="selectin", order_by="PropVersion.id")


class PropVersion(_Version, Base):
    __tablename__ = "prop_versions"

    prop_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("props.id"), index=True)
    based_on_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("prop_versions.id"))
    item_ref: Mapped[str | None] = mapped_column(String(64), comment="PROPD-CHAIR@0.1")
    solid: Mapped[bool] = mapped_column(Boolean, default=True, comment="characters walk around it")
    rests_on: Mapped[str] = mapped_column(String(8), default="floor", comment="floor|top")
    top_z: Mapped[float | None] = mapped_column(Float, comment="height of its top, if things can stand on it")
    seat_z: Mapped[float | None] = mapped_column(Float, comment="height of its seat, if it has one")

    owner: Mapped[Prop] = relationship(back_populates="versions", foreign_keys=[prop_id])

    @property
    def owner_id(self) -> uuid.UUID:
        return self.prop_id


class Environment(_Identity, Base):
    __tablename__ = "environments"

    template_key: Mapped[str] = mapped_column(String(32), comment="living_room|bedroom|classroom|park|street")
    kind: Mapped[str] = mapped_column(String(8), comment="room|outdoor")
    recommended_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("environment_versions.id", use_alter=True, name="fk_environments_recommended_version_id"))

    versions: Mapped[list[EnvironmentVersion]] = relationship(
        back_populates="owner", foreign_keys="EnvironmentVersion.environment_id", lazy="selectin",
        order_by="EnvironmentVersion.id")

    @property
    def category(self) -> str:
        return self.template_key


class EnvironmentVersion(_Version, Base):
    __tablename__ = "environment_versions"

    environment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("environments.id"), index=True)
    based_on_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("environment_versions.id"))
    template_ref: Mapped[str | None] = mapped_column(String(64), comment="ENVD-LIVING_ROOM@0.1")
    lighting: Mapped[str | None] = mapped_column(String(32), comment="lighting preset")
    layout: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="ResolvedEnvironment.layout(): solids, tops, seats, marks, cameras")

    owner: Mapped[Environment] = relationship(back_populates="versions", foreign_keys=[environment_id])

    @property
    def owner_id(self) -> uuid.UUID:
        return self.environment_id
