"""Character library service: identity, drafts, review and the publish request (architecture 6).

Lifecycle status and the version number live on ``asset_versions_registry``; the build and
review record live on ``character_versions``. The database triggers (migration 0005) are the
last line: this module refuses earlier with a clear error, and never relies on them for flow.

Write order matters because the version trigger reads the registry status: a change that is
only allowed in the current status is flushed before the status moves.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assets import registry
from app.core.errors import ApiError
from app.models.assets import AssetFile
from app.models.characters import Character, CharacterVersion
from studio_contracts.character import CharacterSpecification, ResolvedCharacter, resolve_spec, validate_spec
from studio_contracts.ids import AssetKind, uuid7

#: files a build must have before it can go to review
REQUIRED_ROLES = ("blend", "spec", "report", "thumbnail", "glb")


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- specs
def checked_spec(raw: dict[str, Any]) -> tuple[dict[str, Any], ResolvedCharacter]:
    """Validate a specification; 422 lists every issue at once."""
    try:
        spec = CharacterSpecification.model_validate(raw)
    except ValidationError as exc:
        issues = [{"path": ".".join(str(p) for p in e["loc"]) or "$", "code": e["type"], "message": e["msg"]}
                  for e in exc.errors(include_url=False, include_context=False)]
        raise ApiError(422, "invalid_spec", "the character specification is not valid", issues=issues) from None
    errors = [i for i in validate_spec(spec) if i.severity == "error"]
    if errors:
        raise ApiError(422, "invalid_spec", "the character specification is not valid",
                       issues=[i.model_dump() for i in errors])
    return spec.model_dump(mode="json"), resolve_spec(spec)


def _apply_spec(version: CharacterVersion, spec: dict[str, Any], rc: ResolvedCharacter) -> None:
    version.spec = spec
    version.spec_digest = rc.digest()
    version.template_ref = rc.template
    version.style_profile_ref = rc.style_profile
    version.rig_profile_ref = rc.rig_profile
    version.fit_profile = rc.fit_profile
    version.height_m = rc.height_m


# ---------------------------------------------------------------- lookups
def get_character(session: Session, character_id: uuid.UUID, *, lock: bool = False) -> Character:
    q = select(Character).where(Character.id == character_id)
    if lock:
        q = q.with_for_update(of=Character)
    c = session.scalar(q)
    if c is None:
        raise ApiError(404, "character_not_found", str(character_id))
    return c


def get_version(session: Session, character_id: uuid.UUID, version_id: uuid.UUID, *,
                lock: bool = False) -> CharacterVersion:
    q = select(CharacterVersion).where(CharacterVersion.id == version_id,
                                       CharacterVersion.character_id == character_id)
    if lock:  # locks the version and its registry row (status) together; entry is an inner join
        q = q.with_for_update().execution_options(populate_existing=True)
    v = session.scalar(q)
    if v is None:
        raise ApiError(404, "character_version_not_found", str(version_id))
    return v


def _need(version: CharacterVersion, *statuses: str) -> None:
    if version.status not in statuses:
        raise ApiError(409, "invalid_version_status",
                       f"the version is {version.status}; this needs {' or '.join(statuses)}",
                       version_status=version.status)


# ---------------------------------------------------------------- identity and drafts
def create_character(session: Session, *, name: str, description: str, tags: list[str], spec: dict[str, Any],
                     created_by: uuid.UUID | None) -> tuple[Character, CharacterVersion]:
    clean, rc = checked_spec({**spec, "name": spec.get("name") or name})
    character = Character(id=uuid7(), code=registry.allocate_code(session, AssetKind.character), name=name,
                          description=description, tags=sorted(set(tags)), created_by=created_by)
    session.add(character)
    session.flush()
    return character, _new_version(session, character, clean, rc, created_by=created_by)


def _new_version(session: Session, character: Character, spec: dict[str, Any], rc: ResolvedCharacter, *,
                 created_by: uuid.UUID | None, based_on: uuid.UUID | None = None) -> CharacterVersion:
    reg = registry.create_draft(session, AssetKind.character, asset_id=character.id, code=character.code,
                                created_by=created_by)
    version = CharacterVersion(id=reg.id, character_id=character.id, based_on_version_id=based_on, review_notes="")
    _apply_spec(version, spec, rc)
    session.add(version)
    session.flush()
    session.refresh(version)
    return version


def new_draft(session: Session, character: Character, *, based_on_version_id: uuid.UUID | None,
              spec: dict[str, Any] | None, created_by: uuid.UUID | None) -> CharacterVersion:
    """A new draft, either copying an existing version's spec (the only way to "edit" a
    published version) or from a given spec. The source version is never touched."""
    if based_on_version_id is not None:
        source = get_version(session, character.id, based_on_version_id)
        raw = spec if spec is not None else source.spec
    elif spec is not None:
        raw = spec
    else:
        raise ApiError(422, "spec_required", "give based_on_version_id or a spec")
    clean, rc = checked_spec(raw)
    return _new_version(session, character, clean, rc, created_by=created_by, based_on=based_on_version_id)


def update_draft(session: Session, version: CharacterVersion, spec: dict[str, Any]) -> CharacterVersion:
    _need(version, "draft")
    clean, rc = checked_spec(spec)
    _apply_spec(version, clean, rc)
    if version.review_decision == "reject":
        version.review_decision = None  # notes stay until the next review, for the author
    session.flush()
    return version


def discard_draft(session: Session, version: CharacterVersion) -> None:
    """Delete a draft and its file rows. Work-bucket objects are left to the draft GC."""
    _need(version, "draft")
    referenced = session.scalar(select(CharacterVersion.id).where(CharacterVersion.based_on_version_id == version.id))
    if referenced is not None:
        raise ApiError(409, "draft_referenced", "another version is based on this draft")
    for f in list(version.entry.files):
        session.delete(f)
    session.flush()
    reg = version.entry
    session.delete(version)
    session.flush()
    session.delete(reg)
    session.flush()


def begin_generate(version: CharacterVersion, job_id: uuid.UUID) -> None:
    """Mark ``job_id`` as the build this draft waits for; an older job finishing later is ignored."""
    _need(version, "draft")
    version.generate_job_id = job_id


def attach_build(session: Session, version: CharacterVersion, job_id: uuid.UUID, uploaded: list[dict[str, Any]],
                 data: dict[str, Any], blender_version: str | None) -> dict[str, Any]:
    """Record a finished build on its draft, replacing the previous build's file rows.

    Called by the worker in the transaction that finishes the job. A build that is no longer
    wanted (draft submitted, or a newer generate requested) is recorded on the job only."""
    if version.status != "draft":
        return {"attached": False, "reason": f"version is {version.status}"}
    if version.generate_job_id != job_id:
        return {"attached": False, "reason": "a newer build was requested"}
    for f in list(version.entry.files):
        session.delete(f)
    session.flush()
    ids = []
    for u in uploaded:
        f = registry.add_file(session, version.entry, role=u["role"], storage_key=u["storage_key"],
                              sha256=u["sha256"], size_bytes=u["size_bytes"], mime_type=u["mime_type"],
                              blender_version=blender_version, job_id=job_id)
        ids.append(str(f.id))
    version.built_digest = data.get("resolved_digest")
    version.validation_report = data.get("report")
    version.blender_version = blender_version
    if data.get("height_m") is not None:
        version.height_m = data["height_m"]
    session.flush()
    session.expire(version.entry, ["files"])
    return {"attached": True, "asset_file_ids": ids}


# ---------------------------------------------------------------- review
def readiness(version: CharacterVersion) -> list[str]:
    """Why the draft cannot be submitted yet (empty when it can)."""
    problems = []
    if version.built_digest is None:
        problems.append("no build yet: run generate")
    elif version.built_digest != version.spec_digest:
        problems.append("the spec changed since the last build: run generate again")
    report = version.validation_report or {}
    if not report.get("ok"):
        problems.append("the last build did not pass validation")
    files = {f.role: f for f in version.entry.files}
    missing = [r for r in REQUIRED_ROLES if r not in files]
    if missing:
        problems.append(f"missing files: {', '.join(missing)}")
    elif report.get("blend_sha256") != files["blend"].sha256:
        problems.append("the validation report does not describe this character.blend")
    return problems


def submit(session: Session, version: CharacterVersion) -> CharacterVersion:
    _need(version, "draft")
    problems = readiness(version)
    if problems:
        raise ApiError(409, "not_ready_for_review", "; ".join(problems), problems=problems)
    version.review_decision = None
    version.reviewed_by = None
    version.reviewed_at = None
    session.flush()  # while still a draft
    version.entry.status = "review"
    session.flush()
    return version


def review(session: Session, version: CharacterVersion, *, decision: str, notes: str,
           reviewer: uuid.UUID | None) -> CharacterVersion:
    _need(version, "review")
    version.review_decision = decision
    version.review_notes = notes
    version.reviewed_by = reviewer
    version.reviewed_at = now()
    session.flush()
    if decision == "reject":
        version.entry.status = "draft"
        session.flush()
    return version


def check_publishable(version: CharacterVersion) -> None:
    _need(version, "review")
    if version.review_decision != "approve":
        raise ApiError(409, "not_approved", "a reviewer must approve the version before it is published")


def deprecate(session: Session, version: CharacterVersion) -> CharacterVersion:
    _need(version, "published")
    version.entry.status = "deprecated"
    session.flush()
    version.deprecated_at = now()
    session.flush()
    return version


# ---------------------------------------------------------------- files
def files_by_name(version: CharacterVersion) -> dict[str, AssetFile]:
    return {f.storage_key.rsplit("/", 1)[-1]: f for f in version.entry.files}


def bucket_for(version: CharacterVersion) -> str:
    return "library" if version.status in ("published", "deprecated") else "work"
