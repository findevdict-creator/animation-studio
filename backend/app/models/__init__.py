"""ORM models. Import every module here so Alembic sees the whole metadata."""

from app.models.base import Base
from app.models import ai, assets, audit, characters, identity, jobs  # noqa: F401

__all__ = ["Base"]
