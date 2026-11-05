"""Files people upload for the platform to work from: motion-capture takes (BVH) for the
animation library and dialogue recordings for lip sync (and, later, reference images).

An upload is write-once: the bytes live in the work bucket at ``uploads/<id>/<name>`` and
the row records their SHA-256, which every job that uses the file checks again. Rows are
never changed (migration 0009); a specification that names an upload names its key and
its SHA-256.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk

UPLOAD_KINDS = ("bvh", "audio")


class Upload(Base):
    __tablename__ = "uploads"
    __table_args__ = (CheckConstraint(f"kind IN {UPLOAD_KINDS}", name="kind"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(120))
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(String(80))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = created_at()
