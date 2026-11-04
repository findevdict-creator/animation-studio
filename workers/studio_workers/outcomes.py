"""What happens in the database after a job succeeds, per job type.

The supervisor has already validated ``result.json`` and uploaded every declared output to
the work bucket; a handler here records them where they belong (registry drafts, later
character versions, renders, …). It runs in the same transaction that marks the job
succeeded.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.assets import registry
from studio_contracts.ids import AssetKind

Uploaded = dict[str, Any]  # path, role, mime_type, storage_key, sha256, size_bytes
Outcome = Callable[[Session, uuid.UUID, list[Uploaded], dict[str, Any], str | None], dict[str, Any]]


def _draft_with_files(session: Session, kind: AssetKind, job_id: uuid.UUID, uploaded: list[Uploaded],
                      blender_version: str | None, created_by: uuid.UUID | None = None) -> dict[str, Any]:
    version = registry.create_draft(session, kind, created_by=created_by)
    files = []
    for u in uploaded:
        f = registry.add_file(
            session, version, role=u["role"], storage_key=u["storage_key"], sha256=u["sha256"],
            size_bytes=u["size_bytes"], mime_type=u["mime_type"], blender_version=blender_version, job_id=job_id,
        )
        files.append(str(f.id))
    return {"asset_version_id": str(version.id), "display_code": version.display_code, "asset_file_ids": files}


def _requester(session: Session, job_id: uuid.UUID) -> uuid.UUID | None:
    from app.models.jobs import Job

    job = session.get(Job, job_id)
    return job.requested_by if job else None


def _blender_smoke(session: Session, job_id: uuid.UUID, uploaded: list[Uploaded], data: dict[str, Any],
                   blender_version: str | None) -> dict[str, Any]:
    return _draft_with_files(session, AssetKind.test_asset, job_id, uploaded, blender_version)


def _generate_character(session: Session, job_id: uuid.UUID, uploaded: list[Uploaded], data: dict[str, Any],
                        blender_version: str | None) -> dict[str, Any]:
    """A new character draft (CHAR-NNN) holding the build. Character identity and version
    tables, review and publishing arrive with the library (Phase 3)."""
    return _draft_with_files(session, AssetKind.character, job_id, uploaded, blender_version,
                             created_by=_requester(session, job_id))


OUTCOMES: dict[str, Outcome] = {"blender_smoke": _blender_smoke, "generate_character": _generate_character}
