"""Animation library service: identity, drafts, review and publish (SRS 30 Phase 6).

An animation is its own asset with its own versions (ANIM-001). The lifecycle is the one
characters, outfits and accessories have: draft -> generate -> submit -> review -> publish,
with the database triggers (migration 0009) as the last line. Which characters an animation
plays on is decided from its compatibility record (``AnimationCompat``: rig profile, keyed
controls, declared body-fit profiles) when a scene pins it; the build has already played it
on reference bodies of other proportions, and the scene's reopen check measures it again on
the actual character.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from studio_contracts.animation import (
    AnimationSpecification,
    ResolvedAnimation,
    resolve_animation,
    validate_animation,
)
from studio_contracts.ids import AssetKind, uuid7

from app.assets import registry
from app.core.errors import ApiError
from app.models.animations import Animation, AnimationVersion
from app.models.assets import AssetFile

REQUIRED_ROLES = ("blend", "spec", "report", "thumbnail")
USABLE_STATUSES = ("published", "deprecated")


def now() -> datetime:
    return datetime.now(UTC)


def checked_spec(raw: dict[str, Any]) -> tuple[dict[str, Any], ResolvedAnimation]:
    """Validate an animation specification; 422 lists every issue at once."""
    try:
        spec = AnimationSpecification.model_validate(raw)
    except ValidationError as exc:
        issues = [{"path": ".".join(str(p) for p in e["loc"]) or "$", "code": e["type"], "message": e["msg"]}
                  for e in exc.errors(include_url=False, include_context=False)]
        raise ApiError(422, "invalid_spec", "the animation specification is not valid", issues=issues) from None
    errors = [i for i in validate_animation(spec) if i.severity == "error"]
    if errors:
        raise ApiError(422, "invalid_spec", "the animation specification is not valid",
                       issues=[i.model_dump() for i in errors])
    return spec.model_dump(mode="json"), resolve_animation(spec)


def _apply_spec(version: AnimationVersion, spec: dict[str, Any], ra: ResolvedAnimation) -> None:
    version.spec = spec
    version.spec_digest = ra.digest()
    version.style_profile_ref = ra.style_profile
    version.rig_profile_ref = ra.rig_profile
    version.source_kind = ra.source_kind
    version.generator_ref = ra.generator
    version.source_upload_key = ra.bvh.upload_key if ra.bvh else None
    version.source_sha256 = ra.bvh.sha256 if ra.bvh else None
    version.licence = ra.bvh.licence if ra.bvh else None
    version.fit_profiles = list(ra.fit_profiles)


def get_animation(session: Session, animation_id: uuid.UUID, *, lock: bool = False) -> Animation:
    q = select(Animation).where(Animation.id == animation_id)
    if lock:
        q = q.with_for_update(of=Animation)
    a = session.scalar(q)
    if a is None:
        raise ApiError(404, "animation_not_found", str(animation_id))
    return a


def get_version(session: Session, animation_id: uuid.UUID, version_id: uuid.UUID, *,
                lock: bool = False) -> AnimationVersion:
    q = select(AnimationVersion).where(AnimationVersion.id == version_id, AnimationVersion.animation_id == animation_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    v = session.scalar(q)
    if v is None:
        raise ApiError(404, "animation_version_not_found", str(version_id))
    return v


def _need(version: AnimationVersion, *statuses: str) -> None:
    if version.status not in statuses:
        raise ApiError(409, "invalid_version_status",
                       f"the version is {version.status}; this needs {' or '.join(statuses)}",
                       version_status=version.status)


def _same_category(animation: Animation, ra: ResolvedAnimation) -> None:
    if ra.category != animation.category:
        raise ApiError(422, "category_changed", "an animation's category never changes; create a new animation",
                       category=animation.category)


# ---------------------------------------------------------------- identity and drafts
def create_animation(session: Session, *, name: str, description: str, tags: list[str], spec: dict[str, Any],
                     created_by: uuid.UUID | None) -> tuple[Animation, AnimationVersion]:
    clean, ra = checked_spec({**spec, "name": spec.get("name") or name})
    anim = Animation(id=uuid7(), code=registry.allocate_code(session, AssetKind.animation), name=name,
                    description=description, tags=sorted(set(tags)), category=ra.category, created_by=created_by)
    session.add(anim)
    session.flush()
    return anim, _new_version(session, anim, clean, ra, created_by=created_by)


def _new_version(session: Session, anim: Animation, spec: dict[str, Any], ra: ResolvedAnimation, *,
                 created_by: uuid.UUID | None, based_on: uuid.UUID | None = None) -> AnimationVersion:
    reg = registry.create_draft(session, AssetKind.animation, asset_id=anim.id, code=anim.code, created_by=created_by)
    version = AnimationVersion(id=reg.id, animation_id=anim.id, based_on_version_id=based_on, review_notes="")
    _apply_spec(version, spec, ra)
    session.add(version)
    session.flush()
    session.refresh(version)
    return version


def new_draft(session: Session, anim: Animation, *, based_on_version_id: uuid.UUID | None,
              spec: dict[str, Any] | None, created_by: uuid.UUID | None) -> AnimationVersion:
    if based_on_version_id is not None:
        source = get_version(session, anim.id, based_on_version_id)
        raw = spec if spec is not None else source.spec
    elif spec is not None:
        raw = spec
    else:
        raise ApiError(422, "spec_required", "give based_on_version_id or a spec")
    clean, ra = checked_spec({"name": anim.name, **raw})
    _same_category(anim, ra)
    return _new_version(session, anim, clean, ra, created_by=created_by, based_on=based_on_version_id)


def update_draft(session: Session, version: AnimationVersion, spec: dict[str, Any]) -> AnimationVersion:
    _need(version, "draft")
    clean, ra = checked_spec({"name": version.animation.name, **spec})
    _same_category(version.animation, ra)
    _apply_spec(version, clean, ra)
    if version.review_decision == "reject":
        version.review_decision = None
    session.flush()
    return version


def discard_draft(session: Session, version: AnimationVersion) -> None:
    _need(version, "draft")
    referenced = session.scalar(select(AnimationVersion.id).where(AnimationVersion.based_on_version_id == version.id))
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


def check_source_available(session: Session, version: AnimationVersion) -> None:
    """A BVH source must be an upload of that exact file (key and SHA-256)."""
    from app.models.uploads import Upload

    if version.source_kind != "bvh":
        return
    up = session.scalar(select(Upload).where(Upload.storage_key == version.source_upload_key))
    if up is None or up.sha256 != version.source_sha256 or up.kind != "bvh":
        raise ApiError(422, "source_not_uploaded", "the BVH take named by the specification is not an upload "
                       "with that key and SHA-256", upload_key=version.source_upload_key)


def begin_generate(version: AnimationVersion, job_id: uuid.UUID) -> None:
    _need(version, "draft")
    version.generate_job_id = job_id


def attach_build(session: Session, version: AnimationVersion, job_id: uuid.UUID, uploaded: list[dict[str, Any]],
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
    version.compat = data.get("compat")
    version.blender_version = blender_version
    session.flush()
    session.expire(version.entry, ["files"])
    return {"attached": True, "asset_file_ids": ids}


# ---------------------------------------------------------------- review
def readiness(version: AnimationVersion) -> list[str]:
    problems = []
    if version.built_digest is None:
        problems.append("no build yet: run generate")
    elif version.built_digest != version.spec_digest:
        problems.append("the spec changed since the last build: run generate again")
    report = version.check_report or {}
    if version.built_digest is not None and not report.get("ok"):
        failed = ", ".join(report.get("failed", [])[:6])
        problems.append(f"the play check on the reference bodies failed ({failed})" if failed
                        else "the play check on the reference bodies did not pass")
    files = {f.role: f for f in version.entry.files}
    missing = [r for r in REQUIRED_ROLES if r not in files]
    if missing:
        problems.append(f"missing files: {', '.join(missing)}")
    elif report.get("animation_blend_sha256") != files["blend"].sha256:
        problems.append("the check report does not describe this animation.blend")
    elif version.compat is None:
        problems.append("the build recorded no compatibility record")
    return problems


def submit(session: Session, version: AnimationVersion) -> AnimationVersion:
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


def review(session: Session, version: AnimationVersion, *, decision: str, notes: str,
           reviewer: uuid.UUID | None) -> AnimationVersion:
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


def check_publishable(version: AnimationVersion) -> None:
    _need(version, "review")
    if version.review_decision != "approve":
        raise ApiError(409, "not_approved", "a reviewer must approve the version before it is published")


def deprecate(session: Session, version: AnimationVersion) -> AnimationVersion:
    _need(version, "published")
    version.entry.status = "deprecated"
    session.flush()
    version.deprecated_at = now()
    session.flush()
    return version


# ---------------------------------------------------------------- files
def files_by_name(version: AnimationVersion) -> dict[str, AssetFile]:
    return {f.storage_key.rsplit("/", 1)[-1]: f for f in version.entry.files}


def bucket_for(version: AnimationVersion) -> str:
    return "library" if version.status in USABLE_STATUSES else "work"


# ---------------------------------------------------------------- publishing
def next_number(session: Session, version: AnimationVersion, bump: str) -> tuple[int, int, list[str]]:
    """1.0 first; otherwise the latest published number bumped. Anything that can stop a
    scene that pins the previous version from playing this one is major: another rig or
    style profile, keying controls or face properties the previous one did not, a narrower
    set of fit profiles, or a change of loop or travel."""
    published = [v for v in version.animation.versions
                 if v.status in USABLE_STATUSES and v.id != version.id]
    if not published:
        return 1, 0, []

    def narrower(old: list[str], new: list[str]) -> bool:
        return bool(new) and (not old or not set(old) <= set(new))

    latest = max(published, key=lambda v: (v.entry.major, v.entry.minor))
    a, b = latest.compat or {}, version.compat or {}
    reasons = [what for what, changed in (
        ("rig profile", latest.rig_profile_ref != version.rig_profile_ref),
        ("style profile", latest.style_profile_ref != version.style_profile_ref),
        ("controls", not set(b.get("controls", [])) <= set(a.get("controls", []))),
        ("face properties", not set(b.get("face_props", [])) <= set(a.get("face_props", []))),
        ("loop or travel", (a.get("loops"), a.get("travels")) != (b.get("loops"), b.get("travels"))),
        ("body-fit profiles", narrower(latest.fit_profiles, version.fit_profiles))) if changed]
    if reasons or bump == "major":
        return latest.entry.major + 1, 0, reasons
    return latest.entry.major, latest.entry.minor + 1, []


def library_prefix(version: AnimationVersion, major: int, minor: int) -> str:
    return f"library/animations/{version.animation_id}/versions/{major}.{minor}/"


def complete_publish(session: Session, version: AnimationVersion, *, major: int, minor: int,
                     files: list[dict[str, Any]], manifest: dict[str, Any], published_by: uuid.UUID | None,
                     expected_build: str, job_id: uuid.UUID) -> AnimationVersion:
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
    anim = version.animation
    if anim.recommended_version_id is None:
        anim.recommended_version_id = version.id
        session.flush()
    session.expire(version.entry, ["files"])
    return version
