"""The generic asset version registry, files and dependencies (architecture section 3.2).

Every kind-specific version table (``character_versions``, ``outfit_versions``, …) shares
its primary key with a row here, so ``asset_files``, ``asset_dependencies`` and later
``episode_assets`` can use real foreign keys across all kinds. Immutability of published
rows is enforced by triggers created in the baseline migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at, uuid_pk

VERSION_STATUSES = ("draft", "review", "published", "deprecated")
FILE_ROLES = ("blend", "spec", "thumbnail", "turntable", "glb", "texture", "manifest", "log", "report", "preview")


class AssetCodeCounter(Base):
    """Next display-code number per kind; codes are never reused."""

    __tablename__ = "asset_code_counters"

    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    next_number: Mapped[int] = mapped_column(Integer, default=1)


class AssetVersion(Base):
    __tablename__ = "asset_versions_registry"
    __table_args__ = (
        CheckConstraint(f"status IN {VERSION_STATUSES}", name="status"),
        CheckConstraint("(major IS NULL) = (minor IS NULL)", name="version_pair"),
        CheckConstraint("status IN ('draft','review') OR major IS NOT NULL", name="published_has_number"),
        UniqueConstraint("asset_id", "major", "minor", name="uq_asset_versions_registry_number"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(32), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, comment="the identity row's id")
    display_code: Mapped[str] = mapped_column(String(32), index=True, comment="CHAR-001")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    major: Mapped[int | None] = mapped_column(Integer)
    minor: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = created_at()

    files: Mapped[list["AssetFile"]] = relationship(back_populates="version", lazy="selectin")

    @property
    def version_label(self) -> str | None:
        return None if self.major is None else f"{self.major}.{self.minor}"


class AssetFile(Base):
    __tablename__ = "asset_files"
    __table_args__ = (
        CheckConstraint(f"role IN {FILE_ROLES}", name="role"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint("size_bytes >= 0", name="size"),
        UniqueConstraint("version_id", "storage_key", name="uq_asset_files_version_key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("asset_versions_registry.id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(String(127))
    blender_version: Mapped[str | None] = mapped_column(String(32))
    created_by_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    created_at: Mapped[datetime] = created_at()

    version: Mapped[AssetVersion] = relationship(back_populates="files")


class AssetDependency(Base):
    __tablename__ = "asset_dependencies"
    __table_args__ = (CheckConstraint("from_version_id <> to_version_id", name="no_self"),)

    from_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("asset_versions_registry.id", ondelete="CASCADE"), primary_key=True
    )
    to_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("asset_versions_registry.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    dependency_type: Mapped[str] = mapped_column(String(32), primary_key=True)
