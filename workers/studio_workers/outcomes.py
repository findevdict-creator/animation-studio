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


def _blender_smoke(session: Session, job_id: uuid.UUID, uploaded: list[Uploaded], data: dict[str, Any],
                   blender_version: str | None) -> dict[str, Any]:
    return _draft_with_files(session, AssetKind.test_asset, job_id, uploaded, blender_version)


def _generate_character(session: Session, job_id: uuid.UUID, uploaded: list[Uploaded], data: dict[str, Any],
                        blender_version: str | None) -> dict[str, Any]:
    """The build goes onto its character draft. A job submitted without a draft (the Phase 2
    path) creates the character identity and its first draft from the job's spec."""
    from app.assets import characters
    from app.models.characters import CharacterVersion
    from app.models.jobs import Job

    job = session.get(Job, job_id)
    draft_id = job.params.get("draft_version_id") if job else None
    if draft_id:
        version = session.get(CharacterVersion, uuid.UUID(draft_id), with_for_update=True, populate_existing=True)
        if version is None:
            return {"attached": False, "reason": "the draft was discarded"}
    else:
        spec = job.params["spec"]
        _, version = characters.create_character(session, name=spec["name"], description=spec.get("description", ""),
                                                 tags=[], spec=spec, created_by=job.requested_by)
        characters.begin_generate(version, job_id)
    recorded = characters.attach_build(session, version, job_id, uploaded, data, blender_version)
    return {"character_id": str(version.character_id), "asset_version_id": str(version.id),
            "display_code": version.entry.display_code, **recorded}


OUTCOMES: dict[str, Outcome] = {"blender_smoke": _blender_smoke, "generate_character": _generate_character}
