"""The generic asset registry: display codes, draft versions and their files.

Phase 1 uses it for the smoke job's ``test_asset`` drafts; Phase 3 builds publishing on it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.assets import AssetFile, AssetVersion
from studio_contracts.ids import AssetKind, display_code, uuid7


def allocate_code(session: Session, kind: AssetKind) -> str:
    """Next display code for ``kind``; concurrent callers serialise on the counter row and
    numbers are never reused (a rolled-back transaction gives its number back, which is
    fine: nothing outside the transaction ever saw it)."""
    number = session.execute(
        text(
            "INSERT INTO asset_code_counters (kind, next_number) VALUES (:k, 2) "
            "ON CONFLICT (kind) DO UPDATE SET next_number = asset_code_counters.next_number + 1 "
            "RETURNING next_number - 1"
        ),
        {"k": kind.value},
    ).scalar_one()
    return display_code(kind, number)


def create_draft(session: Session, kind: AssetKind, asset_id: uuid.UUID | None = None, code: str | None = None,
                 created_by: uuid.UUID | None = None) -> AssetVersion:
    version = AssetVersion(
        id=uuid7(), kind=kind.value, asset_id=asset_id or uuid7(), display_code=code or allocate_code(session, kind),
        status="draft", created_by=created_by,
    )
    session.add(version)
    session.flush()
    return version


def add_file(session: Session, version: AssetVersion, *, role: str, storage_key: str, sha256: str, size_bytes: int,
             mime_type: str, blender_version: str | None = None, job_id: uuid.UUID | None = None) -> AssetFile:
    f = AssetFile(
        id=uuid7(), version_id=version.id, role=role, storage_key=storage_key, sha256=sha256, size_bytes=size_bytes,
        mime_type=mime_type, blender_version=blender_version, created_by_job_id=job_id,
    )
    session.add(f)
    session.flush()
    return f
