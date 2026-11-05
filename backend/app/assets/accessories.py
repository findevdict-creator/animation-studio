"""Accessory library service: identity, drafts, review and publish (SRS 8, architecture 5.3).

An accessory is its own asset with its own versions (ACC-001). The lifecycle is the one
characters and outfits have: draft -> generate -> submit -> review -> publish, with the
database triggers (migration 0007) as the last line. Which characters an accessory goes on
is decided from its compatibility metadata (rig profile, attachment points, declared
body-fit profiles) when a scene pins it; the attach check on the actual character runs
when the scene is assembled.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from studio_contracts.accessory import (
    AccessorySpecification,
    ResolvedAccessory,
    resolve_accessory,
    validate_accessory,
)
from studio_contracts.ids import AssetKind, uuid7

from app.assets import registry
from app.core.errors import ApiError
from app.models.accessories import Accessory, AccessoryVersion
from app.models.assets import AssetFile

REQUIRED_ROLES = ("blend", "spec", "report", "thumbnail")
USABLE_STATUSES = ("published", "deprecated")


def now() -> datetime:
    return datetime.now(UTC)


def checked_spec(raw: dict[str, Any]) -> tuple[dict[str, Any], ResolvedAccessory]:
    """Validate an accessory specification; 422 lists every issue at once."""
    try:
        spec = AccessorySpecification.model_validate(raw)
    except ValidationError as exc:
        issues = [{"path": ".".join(str(p) for p in e["loc"]) or "$", "code": e["type"], "message": e["msg"]}
                  for e in exc.errors(include_url=False, include_context=False)]
        raise ApiError(422, "invalid_spec", "the accessory specification is not valid", issues=issues) from None
    errors = [i for i in validate_accessory(spec) if i.severity == "error"]
    if errors:
        raise ApiError(422, "invalid_spec", "the accessory specification is not valid",
                       issues=[i.model_dump() for i in errors])
    return spec.model_dump(mode="json"), resolve_accessory(spec)


def _apply_spec(version: AccessoryVersion, spec: dict[str, Any], ra: ResolvedAccessory) -> None:
    version.spec = spec
    version.spec_digest = ra.digest()
    version.style_profile_ref = ra.style_profile
    version.rig_profile_ref = ra.rig_profile
    version.catalog_ref = ra.catalog
    version.item_ref = ra.item
    version.attach_point = ra.attach_point
    version.attach_points = list(ra.attach_points)
    version.fit_profiles = list(ra.fit_profiles)
    version.conflicts_with_slots = list(ra.conflicts_with_slots)


def get_accessory(session: Session, accessory_id: uuid.UUID, *, lock: bool = False) -> Accessory:
    q = select(Accessory).where(Accessory.id == accessory_id)
    if lock:
        q = q.with_for_update(of=Accessory)
    a = session.scalar(q)
    if a is None:
        raise ApiError(404, "accessory_not_found", str(accessory_id))
    return a


def get_version(session: Session, accessory_id: uuid.UUID, version_id: uuid.UUID, *,
                lock: bool = False) -> AccessoryVersion:
    q = select(AccessoryVersion).where(AccessoryVersion.id == version_id, AccessoryVersion.accessory_id == accessory_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    v = session.scalar(q)
    if v is None:
        raise ApiError(404, "accessory_version_not_found", str(version_id))
    return v


def _need(version: AccessoryVersion, *statuses: str) -> None:
    if version.status not in statuses:
        raise ApiError(409, "invalid_version_status",
                       f"the version is {version.status}; this needs {' or '.join(statuses)}",
                       version_status=version.status)


def _same_category(accessory: Accessory, ra: ResolvedAccessory) -> None:
    if ra.category != accessory.category:
        raise ApiError(422, "category_changed", "an accessory's category never changes; create a new accessory",
                       category=accessory.category)


# ---------------------------------------------------------------- identity and drafts
def create_accessory(session: Session, *, name: str, description: str, tags: list[str], spec: dict[str, Any],
                     created_by: uuid.UUID | None) -> tuple[Accessory, AccessoryVersion]:
    clean, ra = checked_spec({**spec, "name": spec.get("name") or name})
    acc = Accessory(id=uuid7(), code=registry.allocate_code(session, AssetKind.accessory), name=name,
                    description=description, tags=sorted(set(tags)), category=ra.category, created_by=created_by)
    session.add(acc)
    session.flush()
    return acc, _new_version(session, acc, clean, ra, created_by=created_by)


def _new_version(session: Session, acc: Accessory, spec: dict[str, Any], ra: ResolvedAccessory, *,
                 created_by: uuid.UUID | None, based_on: uuid.UUID | None = None) -> AccessoryVersion:
    reg = registry.create_draft(session, AssetKind.accessory, asset_id=acc.id, code=acc.code, created_by=created_by)
    version = AccessoryVersion(id=reg.id, accessory_id=acc.id, based_on_version_id=based_on, review_notes="")
    _apply_spec(version, spec, ra)
    session.add(version)
    session.flush()
    session.refresh(version)
    return version


def new_draft(session: Session, acc: Accessory, *, based_on_version_id: uuid.UUID | None,
              spec: dict[str, Any] | None, created_by: uuid.UUID | None) -> AccessoryVersion:
    if based_on_version_id is not None:
        source = get_version(session, acc.id, based_on_version_id)
        raw = spec if spec is not None else source.spec
    elif spec is not None:
        raw = spec
    else:
        raise ApiError(422, "spec_required", "give based_on_version_id or a spec")
    clean, ra = checked_spec({"name": acc.name, **raw})
    _same_category(acc, ra)
    return _new_version(session, acc, clean, ra, created_by=created_by, based_on=based_on_version_id)


def update_draft(session: Session, version: AccessoryVersion, spec: dict[str, Any]) -> AccessoryVersion:
    _need(version, "draft")
    clean, ra = checked_spec({"name": version.accessory.name, **spec})
    _same_category(version.accessory, ra)
    _apply_spec(version, clean, ra)
    if version.review_decision == "reject":
        version.review_decision = None
    session.flush()
    return version


def discard_draft(session: Session, version: AccessoryVersion) -> None:
    _need(version, "draft")
    referenced = session.scalar(select(AccessoryVersion.id).where(AccessoryVersion.based_on_version_id == version.id))
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


def begin_generate(version: AccessoryVersion, job_id: uuid.UUID) -> None:
    _need(version, "draft")
    version.generate_job_id = job_id


def attach_build(session: Session, version: AccessoryVersion, job_id: uuid.UUID, uploaded: list[dict[str, Any]],
                 data: dict[str, Any], blender_version: str | None) -> dict[str, Any]:
    """Record a finished build on its draft, replacing the previous build's files."""
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
    version.check_report = data.get("report")
    version.blender_version = blender_version
    session.flush()
    session.expire(version.entry, ["files"])
    return {"attached": True, "asset_file_ids": ids}


# ---------------------------------------------------------------- review
def readiness(version: AccessoryVersion) -> list[str]:
    problems = []
    if version.built_digest is None:
        problems.append("no build yet: run generate")
    elif version.built_digest != version.spec_digest:
        problems.append("the spec changed since the last build: run generate again")
    report = version.check_report or {}
    if version.built_digest is not None and not report.get("ok"):
        failed = ", ".join(report.get("failed", [])[:6])
        problems.append(f"the attach check on the reference body failed ({failed})" if failed
                        else "the attach check on the reference body did not pass")
    files = {f.role: f for f in version.entry.files}
    missing = [r for r in REQUIRED_ROLES if r not in files]
    if missing:
        problems.append(f"missing files: {', '.join(missing)}")
    elif report.get("accessory_blend_sha256") != files["blend"].sha256:
        problems.append("the attach report does not describe this accessory.blend")
    return problems


def submit(session: Session, version: AccessoryVersion) -> AccessoryVersion:
    _need(version, "draft")
    problems = readiness(version)
    if problems:
        raise ApiError(409, "not_ready_for_review", "; ".join(problems), problems=problems)
    version.review_decision = None
    version.reviewed_by = None
    version.reviewed_at = None
    session.flush()
    version.entry.status = "review"
    session.flush()
    return version


def review(session: Session, version: AccessoryVersion, *, decision: str, notes: str,
           reviewer: uuid.UUID | None) -> AccessoryVersion:
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


def check_publishable(version: AccessoryVersion) -> None:
    _need(version, "review")
    if version.review_decision != "approve":
        raise ApiError(409, "not_approved", "a reviewer must approve the version before it is published")


def deprecate(session: Session, version: AccessoryVersion) -> AccessoryVersion:
    _need(version, "published")
    version.entry.status = "deprecated"
    session.flush()
    version.deprecated_at = now()
    session.flush()
    return version


# ---------------------------------------------------------------- files
def files_by_name(version: AccessoryVersion) -> dict[str, AssetFile]:
    return {f.storage_key.rsplit("/", 1)[-1]: f for f in version.entry.files}


def bucket_for(version: AccessoryVersion) -> str:
    return "library" if version.status in USABLE_STATUSES else "work"


# ---------------------------------------------------------------- publishing
def next_number(session: Session, version: AccessoryVersion, bump: str) -> tuple[int, int, list[str]]:
    """1.0 first; otherwise the latest published number bumped. A changed rig or style
    profile, a dropped attachment point or a narrower set of fit profiles breaks scenes that
    pin the previous version: major."""
    published = [v for v in version.accessory.versions
                 if v.status in USABLE_STATUSES and v.id != version.id]
    if not published:
        return 1, 0, []

    def narrower(old: list[str], new: list[str]) -> bool:
        return bool(new) and (not old or not set(old) <= set(new))

    latest = max(published, key=lambda v: (v.entry.major, v.entry.minor))
    reasons = [what for what, changed in (
        ("rig profile", latest.rig_profile_ref != version.rig_profile_ref),
        ("style profile", latest.style_profile_ref != version.style_profile_ref),
        ("attachment points", not set(latest.attach_points) <= set(version.attach_points)),
        ("body-fit profiles", narrower(latest.fit_profiles, version.fit_profiles))) if changed]
    if reasons or bump == "major":
        return latest.entry.major + 1, 0, reasons
    return latest.entry.major, latest.entry.minor + 1, []


def library_prefix(version: AccessoryVersion, major: int, minor: int) -> str:
    return f"library/accessories/{version.accessory_id}/versions/{major}.{minor}/"


def complete_publish(session: Session, version: AccessoryVersion, *, major: int, minor: int,
                     files: list[dict[str, Any]], manifest: dict[str, Any], published_by: uuid.UUID | None,
                     expected_build: str, job_id: uuid.UUID) -> AccessoryVersion:
    check_publishable(version)
    if version.built_digest != expected_build:
        raise ApiError(409, "build_changed", "the version's build changed while it was being published")
    for f in list(version.entry.files):
        session.delete(f)
    session.flush()
    for f in files:
        registry.add_file(session, version.entry, role=f["role"], storage_key=f["storage_key"], sha256=f["sha256"],
                          size_bytes=f["size_bytes"], mime_type=f["mime_type"],
                          blender_version=f.get("blender_version"), job_id=job_id)
    version.manifest_key = manifest["storage_key"]
    version.manifest_sha256 = manifest["sha256"]
    version.published_by = published_by
    version.published_at = now()
    session.flush()
    version.entry.major, version.entry.minor, version.entry.status = major, minor, "published"
    session.flush()
    acc = version.accessory
    if acc.recommended_version_id is None:
        acc.recommended_version_id = version.id
        session.flush()
    session.expire(version.entry, ["files"])
    return version
