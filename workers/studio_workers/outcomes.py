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


def _blender_smoke(session: Session, job_id: uuid.UUID, uploaded: list[Uploaded], data: dict[str, Any],
                   blender_version: str | None) -> dict[str, Any]:
    version = registry.create_draft(session, AssetKind.test_asset)
    files = []
    for u in uploaded:
        f = registry.add_file(
            session, version, role=u["role"], storage_key=u["storage_key"], sha256=u["sha256"],
            size_bytes=u["size_bytes"], mime_type=u["mime_type"], blender_version=blender_version, job_id=job_id,
        )
        files.append(str(f.id))
    return {"asset_version_id": str(version.id), "display_code": version.display_code, "asset_file_ids": files}


OUTCOMES: dict[str, Outcome] = {"blender_smoke": _blender_smoke}
