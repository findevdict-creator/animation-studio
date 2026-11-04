"""Outfit library service: identity, drafts, review, publish, and the compatibility registry
(SRS 7, architecture 6).

An outfit is its own asset with its own versions; it never copies a character. Its scope
says who it is for: one character (``character``, with an owner), everyone of one body-fit
profile (``body_profile``) or several profiles (``multi``). Which character versions it
actually fits is recorded per pair in ``outfit_compatibility``, from a ``fit_outfit`` job's
report on the exact files:

    candidate --fit job--> verified | failed --review--> approved --> revoked
                                                   (re-check) <-------'

The lifecycle mirrors characters (``app.assets.characters``): the database triggers
(migration 0006) are the last line, this module refuses earlier with a clear error.
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
from app.models.outfits import Outfit, OutfitCompatibility, OutfitVersion
from studio_contracts.ids import AssetKind, uuid7
from studio_contracts.outfit import OutfitSpecification, ResolvedOutfit, resolve_outfit, validate_outfit

#: files a build must have before it can go to review
REQUIRED_ROLES = ("blend", "spec", "report", "thumbnail", "glb")
#: character versions an outfit can be fitted to: their files never change
WEARABLE_STATUSES = ("published", "deprecated")
GOOD_FIT = ("verified", "approved")


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- specs
def checked_spec(raw: dict[str, Any]) -> tuple[dict[str, Any], ResolvedOutfit]:
    """Validate an outfit specification; 422 lists every issue at once."""
    try:
        spec = OutfitSpecification.model_validate(raw)
    except ValidationError as exc:
        issues = [{"path": ".".join(str(p) for p in e["loc"]) or "$", "code": e["type"], "message": e["msg"]}
                  for e in exc.errors(include_url=False, include_context=False)]
        raise ApiError(422, "invalid_spec", "the outfit specification is not valid", issues=issues) from None
    errors = [i for i in validate_outfit(spec) if i.severity == "error"]
    if errors:
        raise ApiError(422, "invalid_spec", "the outfit specification is not valid",
                       issues=[i.model_dump() for i in errors])
    return spec.model_dump(mode="json"), resolve_outfit(spec)


def _apply_spec(version: OutfitVersion, spec: dict[str, Any], ro: ResolvedOutfit) -> None:
    version.spec = spec
    version.spec_digest = ro.digest()
    version.style_profile_ref = ro.style_profile
    version.rig_profile_ref = ro.rig_profile
    version.catalog_ref = ro.catalog
    version.fit_profiles = list(ro.fit_profiles)
    version.primary_fit_profile = ro.primary_fit_profile
    version.slots = sorted({p.slot for p in ro.pieces})


def _owner(session: Session, ro: ResolvedOutfit) -> Character | None:
    if ro.scope != "character":
        return None
    owner = session.scalar(select(Character).where(Character.code == ro.owner_character))
    if owner is None:
        raise ApiError(422, "unknown_owner_character", f"no character {ro.owner_character}",
                       issues=[{"path": "owner_character", "code": "unknown_character",
                                "message": f"no character {ro.owner_character}"}])
    return owner


def _same_scope(outfit: Outfit, ro: ResolvedOutfit, owner: Character | None) -> None:
    if ro.scope != outfit.scope or (owner.id if owner else None) != outfit.owner_character_id:
        raise ApiError(422, "scope_changed", "an outfit's scope and owner never change; create a new outfit",
                       scope=outfit.scope)


# ---------------------------------------------------------------- lookups
def get_outfit(session: Session, outfit_id: uuid.UUID, *, lock: bool = False) -> Outfit:
    q = select(Outfit).where(Outfit.id == outfit_id)
    if lock:
        q = q.with_for_update(of=Outfit)
    o = session.scalar(q)
    if o is None:
        raise ApiError(404, "outfit_not_found", str(outfit_id))
    return o


def get_version(session: Session, outfit_id: uuid.UUID, version_id: uuid.UUID, *,
                lock: bool = False) -> OutfitVersion:
    q = select(OutfitVersion).where(OutfitVersion.id == version_id, OutfitVersion.outfit_id == outfit_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    v = session.scalar(q)
    if v is None:
        raise ApiError(404, "outfit_version_not_found", str(version_id))
    return v


def _need(version: OutfitVersion, *statuses: str) -> None:
    if version.status not in statuses:
        raise ApiError(409, "invalid_version_status",
                       f"the version is {version.status}; this needs {' or '.join(statuses)}",
                       version_status=version.status)


# ---------------------------------------------------------------- identity and drafts
def create_outfit(session: Session, *, name: str, description: str, tags: list[str], spec: dict[str, Any],
                  created_by: uuid.UUID | None) -> tuple[Outfit, OutfitVersion]:
    clean, ro = checked_spec({**spec, "name": spec.get("name") or name})
    owner = _owner(session, ro)
    outfit = Outfit(id=uuid7(), code=registry.allocate_code(session, AssetKind.outfit), name=name,
                    description=description, tags=sorted(set(tags)), scope=ro.scope,
                    owner_character_id=owner.id if owner else None, created_by=created_by)
    session.add(outfit)
    session.flush()
    return outfit, _new_version(session, outfit, clean, ro, created_by=created_by)


def _new_version(session: Session, outfit: Outfit, spec: dict[str, Any], ro: ResolvedOutfit, *,
                 created_by: uuid.UUID | None, based_on: uuid.UUID | None = None) -> OutfitVersion:
    reg = registry.create_draft(session, AssetKind.outfit, asset_id=outfit.id, code=outfit.code,
                                created_by=created_by)
    version = OutfitVersion(id=reg.id, outfit_id=outfit.id, based_on_version_id=based_on, review_notes="")
    _apply_spec(version, spec, ro)
    session.add(version)
    session.flush()
    session.refresh(version)
    return version


def new_draft(session: Session, outfit: Outfit, *, based_on_version_id: uuid.UUID | None,
              spec: dict[str, Any] | None, created_by: uuid.UUID | None) -> OutfitVersion:
    if based_on_version_id is not None:
        source = get_version(session, outfit.id, based_on_version_id)
        raw = spec if spec is not None else source.spec
    elif spec is not None:
        raw = spec
    else:
        raise ApiError(422, "spec_required", "give based_on_version_id or a spec")
    clean, ro = checked_spec({"name": outfit.name, **raw})
    _same_scope(outfit, ro, _owner(session, ro))
    return _new_version(session, outfit, clean, ro, created_by=created_by, based_on=based_on_version_id)


def update_draft(session: Session, version: OutfitVersion, spec: dict[str, Any]) -> OutfitVersion:
    _need(version, "draft")
    clean, ro = checked_spec({"name": version.outfit.name, **spec})
    _same_scope(version.outfit, ro, _owner(session, ro))
    _apply_spec(version, clean, ro)
    if version.review_decision == "reject":
        version.review_decision = None
    session.flush()
    return version


def discard_draft(session: Session, version: OutfitVersion) -> None:
    _need(version, "draft")
    referenced = session.scalar(select(OutfitVersion.id).where(OutfitVersion.based_on_version_id == version.id))
    if referenced is not None:
        raise ApiError(409, "draft_referenced", "another version is based on this draft")
    _drop_fits(session, version)
    for f in list(version.entry.files):
        session.delete(f)
    session.flush()
    reg = version.entry
    session.delete(version)
    session.flush()
    session.delete(reg)
    session.flush()


def begin_generate(version: OutfitVersion, job_id: uuid.UUID) -> None:
    _need(version, "draft")
    version.generate_job_id = job_id


def attach_build(session: Session, version: OutfitVersion, job_id: uuid.UUID, uploaded: list[dict[str, Any]],
                 data: dict[str, Any], blender_version: str | None) -> dict[str, Any]:
    """Record a finished build on its draft, replacing the previous build's files. Fits
    recorded against the previous outfit.blend no longer describe the draft and are dropped."""
    if version.status != "draft":
        return {"attached": False, "reason": f"version is {version.status}"}
    if version.generate_job_id != job_id:
        return {"attached": False, "reason": "a newer build was requested"}
    _drop_fits(session, version)
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
    version.fit_report = data.get("report")
    version.blender_version = blender_version
    session.flush()
    session.expire(version.entry, ["files"])
    return {"attached": True, "asset_file_ids": ids}


def _drop_fits(session: Session, version: OutfitVersion) -> None:
    for row in session.scalars(select(OutfitCompatibility).where(OutfitCompatibility.outfit_version_id == version.id)):
        session.delete(row)
    session.flush()


# ---------------------------------------------------------------- review
def blend_file(version: OutfitVersion) -> AssetFile | None:
    return next((f for f in version.entry.files if f.role == "blend"), None)


def owner_fits(session: Session, version: OutfitVersion) -> list[OutfitCompatibility]:
    """Good fits on a version of the owner character, for the current outfit.blend."""
    outfit = version.outfit
    blend = blend_file(version)
    if outfit.owner_character_id is None or blend is None:
        return []
    q = (select(OutfitCompatibility).join(CharacterVersion, CharacterVersion.id == OutfitCompatibility.character_version_id)
         .where(OutfitCompatibility.outfit_version_id == version.id,
                CharacterVersion.character_id == outfit.owner_character_id,
                OutfitCompatibility.status.in_(GOOD_FIT),
                OutfitCompatibility.outfit_blend_sha256 == blend.sha256))
    return list(session.scalars(q))


def readiness(session: Session, version: OutfitVersion) -> list[str]:
    """Why the draft cannot be submitted yet (empty when it can)."""
    problems = []
    if version.built_digest is None:
        problems.append("no build yet: run generate")
    elif version.built_digest != version.spec_digest:
        problems.append("the spec changed since the last build: run generate again")
    report = version.fit_report or {}
    if version.built_digest is not None and not report.get("ok"):
        failed = ", ".join(report.get("failed", [])[:6])
        problems.append(f"the fit check on the reference body failed ({failed})" if failed
                        else "the fit check on the reference body did not pass")
    files = {f.role: f for f in version.entry.files}
    missing = [r for r in REQUIRED_ROLES if r not in files]
    if missing:
        problems.append(f"missing files: {', '.join(missing)}")
    elif report.get("outfit_blend_sha256") != files["blend"].sha256:
        problems.append("the fit report does not describe this outfit.blend")
    if version.outfit.scope == "character" and not missing and not owner_fits(session, version):
        problems.append("a character-scope outfit needs a verified fit on a published version of its owner: "
                        "run a fit check on the owner")
    return problems


def submit(session: Session, version: OutfitVersion) -> OutfitVersion:
    _need(version, "draft")
    problems = readiness(session, version)
    if problems:
        raise ApiError(409, "not_ready_for_review", "; ".join(problems), problems=problems)
    version.review_decision = None
    version.reviewed_by = None
    version.reviewed_at = None
    session.flush()
    version.entry.status = "review"
    session.flush()
    return version


def review(session: Session, version: OutfitVersion, *, decision: str, notes: str,
           reviewer: uuid.UUID | None) -> OutfitVersion:
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


def check_publishable(version: OutfitVersion) -> None:
    _need(version, "review")
    if version.review_decision != "approve":
        raise ApiError(409, "not_approved", "a reviewer must approve the version before it is published")


def deprecate(session: Session, version: OutfitVersion) -> OutfitVersion:
    _need(version, "published")
    version.entry.status = "deprecated"
    session.flush()
    version.deprecated_at = now()
    session.flush()
    return version


# ---------------------------------------------------------------- files
def files_by_name(version: OutfitVersion) -> dict[str, AssetFile]:
    return {f.storage_key.rsplit("/", 1)[-1]: f for f in version.entry.files}


def bucket_for(version: OutfitVersion) -> str:
    return "library" if version.status in ("published", "deprecated") else "work"


# ---------------------------------------------------------------- publishing
def next_number(session: Session, version: OutfitVersion, bump: str) -> tuple[int, int, list[str]]:
    """1.0 first; otherwise the latest published number bumped. A changed rig profile or
    style, or a dropped body-fit profile, breaks wearers of the previous version: major."""
    published = [v for v in version.outfit.versions
                 if v.status in ("published", "deprecated") and v.id != version.id]
    if not published:
        return 1, 0, []
    latest = max(published, key=lambda v: (v.entry.major, v.entry.minor))
    reasons = [what for what, changed in (
        ("rig profile", latest.rig_profile_ref != version.rig_profile_ref),
        ("style profile", latest.style_profile_ref != version.style_profile_ref),
        ("body-fit profiles", not set(latest.fit_profiles) <= set(version.fit_profiles))) if changed]
    if reasons or bump == "major":
        return latest.entry.major + 1, 0, reasons
    return latest.entry.major, latest.entry.minor + 1, []


def library_prefix(version: OutfitVersion, major: int, minor: int) -> str:
    return f"library/outfits/{version.outfit_id}/versions/{major}.{minor}/"


def complete_publish(session: Session, version: OutfitVersion, *, major: int, minor: int,
                     files: list[dict[str, Any]], manifest: dict[str, Any], published_by: uuid.UUID | None,
                     expected_build: str, job_id: uuid.UUID) -> OutfitVersion:
    """The one transaction that makes an outfit version published. The compatibility rows
    keep pointing at the version; their blend SHA-256 is unchanged by the copy."""
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
    outfit = version.outfit
    if outfit.recommended_version_id is None:
        outfit.recommended_version_id = version.id
        session.flush()
    session.expire(version.entry, ["files"])
    return version


# ---------------------------------------------------------------- compatibility registry
def wearable(session: Session, character_version_id: uuid.UUID) -> CharacterVersion:
    cv = session.get(CharacterVersion, character_version_id)
    if cv is None:
        raise ApiError(404, "character_version_not_found", str(character_version_id))
    if cv.status not in WEARABLE_STATUSES:
        raise ApiError(409, "character_not_published",
                       f"outfits are fitted to published character versions; this one is {cv.status}",
                       version_status=cv.status)
    return cv


def get_fit(session: Session, outfit_version_id: uuid.UUID, character_version_id: uuid.UUID, *,
            lock: bool = False) -> OutfitCompatibility | None:
    q = select(OutfitCompatibility).where(OutfitCompatibility.outfit_version_id == outfit_version_id,
                                          OutfitCompatibility.character_version_id == character_version_id)
    if lock:
        q = q.with_for_update()
    return session.scalar(q)


def check_fittable(session: Session, version: OutfitVersion, character_version_id: uuid.UUID) -> CharacterVersion:
    """Refuse a fit request that cannot run or would overwrite an approval."""
    if blend_file(version) is None:
        raise ApiError(409, "no_build", "the outfit version has no outfit.blend yet: run generate")
    cv = wearable(session, character_version_id)
    if version.outfit.scope == "character" and cv.character_id != version.outfit.owner_character_id:
        raise ApiError(409, "not_the_owner", "a character-scope outfit is only fitted to its owner")
    row = get_fit(session, version.id, cv.id)
    if row is not None and row.status == "approved":
        raise ApiError(409, "fit_approved", "this fit is approved; revoke it before checking again")
    return cv


def begin_fit(session: Session, version: OutfitVersion, cv: CharacterVersion, job_id: uuid.UUID) -> OutfitCompatibility:
    row = get_fit(session, version.id, cv.id, lock=True)
    if row is None:
        row = OutfitCompatibility(id=uuid7(), outfit_version_id=version.id, character_version_id=cv.id, notes="",
                                  files=[])
        session.add(row)
    row.status = "candidate"
    row.fit_job_id = job_id
    row.fit_profile = cv.fit_profile
    row.decided_by = row.decided_at = None
    session.flush()
    return row


def record_fit(session: Session, job_id: uuid.UUID, outfit_version_id: uuid.UUID, character_version_id: uuid.UUID,
               uploaded: list[dict[str, Any]], data: dict[str, Any]) -> dict[str, Any]:
    """The fit job's verdict onto its registry row (the worker's success transaction). A
    result for a row that was re-requested, approved or dropped since is kept on the job only."""
    row = get_fit(session, outfit_version_id, character_version_id, lock=True)
    if row is None:
        return {"recorded": False, "reason": "the outfit draft was rebuilt or discarded"}
    if row.fit_job_id != job_id or row.status != "candidate":
        return {"recorded": False, "reason": f"the fit was re-requested or decided ({row.status})"}
    report = data.get("report") or {}
    row.status = "verified" if data.get("ok") else "failed"
    row.report = report
    row.files = [{k: u[k] for k in ("role", "storage_key", "sha256", "size_bytes", "mime_type")} for u in uploaded]
    row.outfit_blend_sha256 = report.get("outfit_blend_sha256")
    row.character_blend_sha256 = (report.get("wearer") or {}).get("character_blend_sha256")
    row.fit_profile = (report.get("wearer") or {}).get("fit_profile")
    row.fit_profile_declared = report.get("fit_profile_declared")
    row.checked_at = now()
    session.flush()
    return {"recorded": True, "compatibility_id": str(row.id), "status": row.status}


def decide_fit(session: Session, row: OutfitCompatibility, *, decision: str, notes: str,
               who: uuid.UUID | None) -> OutfitCompatibility:
    if decision == "approve" and row.status != "verified":
        raise ApiError(409, "fit_not_verified", f"only a verified fit can be approved; this one is {row.status}",
                       fit_status=row.status)
    if decision == "revoke" and row.status not in ("verified", "approved"):
        raise ApiError(409, "fit_not_good", f"only a verified or approved fit can be revoked; this one is {row.status}",
                       fit_status=row.status)
    row.status = "approved" if decision == "approve" else "revoked"
    row.notes = notes
    row.decided_by = who
    row.decided_at = now()
    session.flush()
    return row
