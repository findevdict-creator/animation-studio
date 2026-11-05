"""ORM models. Import every module here so Alembic sees the whole metadata."""

from app.models import (  # noqa: F401
    accessories,
    ai,
    animations,
    assets,
    audio,
    audit,
    characters,
    identity,
    jobs,
    outfits,
    scenes,
    sets,
    uploads,
)
from app.models.base import Base

__all__ = ["Base"]
