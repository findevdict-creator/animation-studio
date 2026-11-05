"""Animation Library endpoints (SRS 30 Phase 6): the generator catalog and bone maps,
validation, identity, drafts, builds, review and publish.

An animation is its own asset; nothing here touches a character. Where it may play is its
compatibility record (rig profile, keyed controls and face properties, body-fit profiles),
measured by the build; a scene checks that record against each character when it pins the
animation, and the scene's reopen check measures the motion on the actual character.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session
from studio_contracts.animation import (
    BONE_MAPS,
    GENERATORS,
    AnimationSpecification,
    BoneMap,
    GeneratorDef,
    ResolvedAnimation,
    resolve_animation,
    validate_animation,
)
from studio_contracts.character import Issue
from studio_contracts.jobs import AnimationPreviewSettings

from app.api.v1.characters import _pydantic_issues
from app.api.v1.jobs import _out as job_out
from app.api.v1.jobs import submit_and_dispatch
from app.api.v1.library import FileOut, _tags
from app.api.v1.schemas import AssetFileOut, DownloadOut, JobOut, Page
from app.assets import animations as svc
from app.core.auth import Principal, audit, require
from app.core.database import get_session
from app.core.errors import ApiError
from app.core.storage import Storage
from app.models.animations import Animation, AnimationVersion
from app.models.assets import AssetVersion
from app.models.jobs import Job

router = APIRouter(prefix="/animations", tags=["animation library"])
catalog_router = APIRouter(tags=["animation library"])


# ---------------------------------------------------------------- catalog and validation
class AnimationValidationOut(BaseModel):
    valid: bool
    issues: list[Issue]
    resolved: ResolvedAnimation | None = None
    digest: str | None = None


@catalog_router.get("/animation-generators", response_model=list[GeneratorDef],
                    summary="The procedural generators an animation can be made from")
def list_generators(_: Principal = Depends(require("asset.read"))) -> list[GeneratorDef]:
    return list(GENERATORS)


@catalog_router.get("/bone-maps", response_model=list[BoneMap],
                    summary="Motion-capture skeletons a BVH take can be retargeted from")
def list_bone_maps(_: Principal = Depends(require("asset.read"))) -> list[BoneMap]:
    return list(BONE_MAPS.values())


@router.post("/spec/validate", response_model=AnimationValidationOut,
             summary="Validate an animation specification against the generators, bone maps and its style")
def validate(body: dict[str, Any], _: Principal = Depends(require("asset.read"))) -> AnimationValidationOut:
    """Always 200: an invalid spec is a normal answer here, listing every issue."""
    try:
        spec = AnimationSpecification.model_validate(body)
    except ValidationError as exc:
        return AnimationValidationOut(valid=False, issues=_pydantic_issues(exc))
    issues = validate_animation(spec)
    if any(i.severity == "error" for i in issues):
        return AnimationValidationOut(valid=False, issues=issues)
    resolved = resolve_animation(spec)
    return AnimationValidationOut(valid=True, issues=issues, resolved=resolved, digest=resolved.digest())


# ---------------------------------------------------------------- bodies
class AnimationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    spec: dict[str, Any] = Field(description="AnimationSpecification; its name defaults to the animation's")


class AnimationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=20)
    recommended_version_id: uuid.UUID | None = None


class AnimationDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    based_on_version_id: uuid.UUID | None = None
    spec: dict[str, Any] | None = None


class AnimationDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: dict[str, Any]


class AnimationGenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previews: AnimationPreviewSettings = Field(default_factory=AnimationPreviewSettings)


class AnimationReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    notes: str = Field(default="", max_length=4000)


class AnimationPublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bump: Literal["minor", "major"] = Field(
        default="minor", description="major is forced when compatibility narrows (rig, style, controls, loop or "
                                     "travel, fit profiles)")


class AnimationVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    animation_id: uuid.UUID
    status: str
    version_label: str | None
    based_on_version_id: uuid.UUID | None
    spec_digest: str
    built_digest: str | None
    source_kind: str
    generator_ref: str | None
    fit_profiles: list[str]
    review_decision: str | None
    published_at: datetime | None
    deprecated_at: datetime | None
    created_at: datetime | None = None
    created_by: uuid.UUID | None = None
    check_ok: bool | None = None


class AnimationVersionDetail(AnimationVersionSummary):
    spec: dict[str, Any]
    style_profile_ref: str | None
    rig_profile_ref: str | None
    source_upload_key: str | None
    source_sha256: str | None
    licence: str | None
    compat: dict[str, Any] | None
    blender_version: str | None
    check_report: dict[str, Any] | None
    generate_job_id: uuid.UUID | None
    review_notes: str
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    manifest_key: str | None
    manifest_sha256: str | None
    published_by: uuid.UUID | None
    files: list[FileOut] = []
    ready_for_review: list[str] = Field(default_factory=list, description="what blocks submit; empty when ready")


class AnimationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    description: str
    tags: list[str]
    category: str
    lifecycle: str
    recommended_version_id: uuid.UUID | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    recommended_version: str | None = None
    latest_published: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    thumbnail_url: str | None = None


class AnimationDetail(AnimationOut):
    versions: list[AnimationVersionSummary] = []


class AnimationVersionJobOut(BaseModel):
    version: AnimationVersionDetail
    job: JobOut


# ---------------------------------------------------------------- helpers
def _summary(v: AnimationVersion, cls=AnimationVersionSummary) -> Any:
    out = cls.model_validate(v, from_attributes=True)
    out.created_at = v.entry.created_at
    out.created_by = v.entry.created_by
    out.check_ok = (v.check_report or {}).get("ok") if v.check_report is not None else None
    return out


def _detail(v: AnimationVersion) -> AnimationVersionDetail:
    out = _summary(v, AnimationVersionDetail)
    out.files = [FileOut(**AssetFileOut.model_validate(f).model_dump(), name=f.storage_key.rsplit("/", 1)[-1])
                 for f in sorted(v.entry.files, key=lambda f: f.storage_key)]
    out.ready_for_review = svc.readiness(v) if v.status == "draft" else []
    return out


def _thumbnail(a: Animation) -> str | None:
    by_id = {v.id: v for v in a.versions}
    order = ([by_id[a.recommended_version_id]] if a.recommended_version_id in by_id else []) + sorted(
        a.versions, key=lambda v: (v.status == "published", v.id), reverse=True)
    for v in order:
        thumb = next((f for f in v.entry.files if f.role == "thumbnail"), None)
        if thumb is not None:
            return Storage.for_bucket(svc.bucket_for(v)).presign_get(thumb.storage_key)
    return None


def _animation(a: Animation, with_versions: bool = False) -> AnimationOut:
    cls = AnimationDetail if with_versions else AnimationOut
    out = cls.model_validate(a, from_attributes=True)
    counts: dict[str, int] = {}
    for v in a.versions:
        counts[v.status] = counts.get(v.status, 0) + 1
    out.counts = counts
    published = [v for v in a.versions if v.status == "published"]
    if published:
        out.latest_published = max(published, key=lambda v: (v.entry.major, v.entry.minor)).version_label
    rec = next((v for v in a.versions if v.id == a.recommended_version_id), None)
    out.recommended_version = rec.version_label if rec else None
    out.thumbnail_url = _thumbnail(a)
    if with_versions:
        out.versions = [_summary(v) for v in sorted(a.versions, key=lambda v: v.id, reverse=True)]
    return out


def _etag(v: AnimationVersion) -> str:
    return f'"{v.spec_digest[:16]}-{int(v.updated_at.timestamp() * 1_000_000)}"'


def _draft(session: Session, animation_id: uuid.UUID, draft_id: uuid.UUID) -> AnimationVersion:
    v = svc.get_version(session, animation_id, draft_id, lock=True)
    if v.status != "draft":
        raise ApiError(409, "not_a_draft", f"version is {v.status}; published versions never change, "
                       "create a draft based on it instead", version_status=v.status)
    return v


# ---------------------------------------------------------------- identity
@router.post("", status_code=status.HTTP_201_CREATED, response_model=AnimationDetail,
             summary="Create an animation and its first draft from a specification")
def create(body: AnimationCreate, session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.create"))) -> AnimationDetail:
    a, v = svc.create_animation(session, name=body.name, description=body.description, tags=_tags(body.tags),
                                spec=body.spec, created_by=who.user_id)
    audit(session, who, "animation.created", "animation", a.id, code=a.code, draft=str(v.id), category=a.category)
    session.commit()
    session.refresh(a)
    return _animation(a, with_versions=True)


@router.get("", response_model=Page[AnimationOut], summary="Gallery: search and filter animations")
def gallery(session: Session = Depends(get_session), q: str | None = Query(default=None, max_length=80),
            tag: list[str] = Query(default_factory=list), category: str | None = None,
            status_: str | None = Query(default=None, alias="status"),
            source_kind: str | None = Query(default=None, description="generator or bvh"),
            fit_profile: str | None = Query(default=None, description="declared for this profile (or for all)"),
            limit: int = Query(default=48, ge=1, le=200), cursor: uuid.UUID | None = None,
            _: Principal = Depends(require("asset.read"))) -> Page[AnimationOut]:
    query = select(Animation).order_by(Animation.id.desc()).limit(limit + 1)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        query = query.where(or_(Animation.name.ilike(like), Animation.code.ilike(like),
                                Animation.description.ilike(like)))
    if tag:
        query = query.where(Animation.tags.contains(tag))
    if category:
        query = query.where(Animation.category == category)
    if status_ or source_kind or fit_profile:
        sub = select(AnimationVersion.id).join(AssetVersion, AssetVersion.id == AnimationVersion.id).where(
            AnimationVersion.animation_id == Animation.id)
        if status_:
            sub = sub.where(AssetVersion.status == status_)
        if source_kind:
            sub = sub.where(AnimationVersion.source_kind == source_kind)
        if fit_profile:
            sub = sub.where(or_(AnimationVersion.fit_profiles.contains([fit_profile]),
                                AnimationVersion.fit_profiles == []))
        query = query.where(exists(sub))
    if cursor:
        query = query.where(Animation.id < cursor)
    rows = list(session.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    return Page(items=[_animation(a) for a in rows], next_cursor=str(rows[-1].id) if more else None)


@router.get("/{animation_id}", response_model=AnimationDetail)
def detail(animation_id: uuid.UUID, session: Session = Depends(get_session),
           _: Principal = Depends(require("asset.read"))) -> AnimationDetail:
    return _animation(svc.get_animation(session, animation_id), with_versions=True)


@router.patch("/{animation_id}", response_model=AnimationDetail,
              summary="Name, description, tags, recommended version")
def patch_identity(animation_id: uuid.UUID, body: AnimationPatch, session: Session = Depends(get_session),
                   who: Principal = Depends(require("asset.edit_identity"))) -> AnimationDetail:
    a = svc.get_animation(session, animation_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    if "recommended_version_id" in changes:
        rid = changes["recommended_version_id"]
        if rid is not None:
            v = svc.get_version(session, a.id, rid)
            if v.status != "published":
                raise ApiError(409, "not_published", "only a published version can be recommended",
                               version_status=v.status)
        a.recommended_version_id = rid
    if "tags" in changes:
        a.tags = _tags(changes["tags"] or [])
    for field in ("name", "description"):
        if changes.get(field) is not None:
            setattr(a, field, changes[field])
    audit(session, who, "animation.updated", "animation", a.id,
          **{k: str(v) if isinstance(v, uuid.UUID) else v for k, v in changes.items()})
    session.commit()
    session.refresh(a)
    return _animation(a, with_versions=True)


# ---------------------------------------------------------------- versions
@router.get("/{animation_id}/versions", response_model=list[AnimationVersionSummary],
            summary="Version history, newest first")
def history(animation_id: uuid.UUID, session: Session = Depends(get_session),
            _: Principal = Depends(require("asset.read"))) -> list[AnimationVersionSummary]:
    a = svc.get_animation(session, animation_id)
    return [_summary(v) for v in sorted(a.versions, key=lambda v: v.id, reverse=True)]


@router.get("/{animation_id}/versions/{version_id}", response_model=AnimationVersionDetail)
def version_detail(animation_id: uuid.UUID, version_id: uuid.UUID, response: Response,
                   session: Session = Depends(get_session),
                   _: Principal = Depends(require("asset.read"))) -> AnimationVersionDetail:
    v = svc.get_version(session, animation_id, version_id)
    if v.status == "draft":
        response.headers["ETag"] = _etag(v)
    return _detail(v)


@router.get("/{animation_id}/versions/{version_id}/files/{name}", summary="A version file: 302 to a presigned URL",
            responses={302: {"description": "redirect to the object"}, 200: {"model": DownloadOut}})
def version_file(animation_id: uuid.UUID, version_id: uuid.UUID, name: str, redirect: bool = True,
                 session: Session = Depends(get_session), _: Principal = Depends(require("asset.read"))):
    v = svc.get_version(session, animation_id, version_id)
    f = svc.files_by_name(v).get(name)
    if f is None:
        raise ApiError(404, "file_not_found", f"version has no file {name!r}")
    store = Storage.for_bucket(svc.bucket_for(v))
    url = store.presign_get(f.storage_key, filename=name)
    if redirect:
        return RedirectResponse(url, status_code=302)
    return DownloadOut(url=url, expires_in_s=store.presign_expiry_s)


@router.post("/{animation_id}/versions/{version_id}/review", response_model=AnimationVersionDetail,
             summary="Approve, or reject back to draft with notes")
def review(animation_id: uuid.UUID, version_id: uuid.UUID, body: AnimationReviewBody,
           session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.review"))) -> AnimationVersionDetail:
    v = svc.get_version(session, animation_id, version_id, lock=True)
    svc.review(session, v, decision=body.decision, notes=body.notes, reviewer=who.user_id)
    audit(session, who, f"animation.review.{body.decision}", "animation_version", v.id, notes=body.notes)
    session.commit()
    return _detail(v)


@router.post("/{animation_id}/versions/{version_id}/publish", status_code=status.HTTP_202_ACCEPTED,
             response_model=AnimationVersionJobOut, summary="Publish an approved version (job on the publisher queue)")
def publish(animation_id: uuid.UUID, version_id: uuid.UUID, response: Response,
            body: AnimationPublishBody | None = None, session: Session = Depends(get_session),
            idempotency_key: str | None = Header(default=None, max_length=200),
            who: Principal = Depends(require("asset.publish"))) -> AnimationVersionJobOut:
    v = svc.get_version(session, animation_id, version_id, lock=True)
    params = {"version_id": str(v.id), "bump": (body or AnimationPublishBody()).bump}
    replay = idempotency_key and session.scalar(select(Job.id).where(Job.idempotency_key == idempotency_key))
    if not replay:
        svc.check_publishable(v)
    job, created = submit_and_dispatch(session, who, "publish_animation", params, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    session.refresh(v)
    return AnimationVersionJobOut(version=_detail(v), job=job_out(job))


@router.post("/{animation_id}/versions/{version_id}/deprecate", response_model=AnimationVersionDetail,
             summary="Hide a published version from pickers; scenes that pin it still load it")
def deprecate(animation_id: uuid.UUID, version_id: uuid.UUID, session: Session = Depends(get_session),
              who: Principal = Depends(require("asset.publish"))) -> AnimationVersionDetail:
    svc.get_animation(session, animation_id, lock=True)
    v = svc.get_version(session, animation_id, version_id, lock=True)
    svc.deprecate(session, v)
    audit(session, who, "animation.deprecated", "animation_version", v.id, version=v.version_label)
    session.commit()
    return _detail(v)


# ---------------------------------------------------------------- drafts
@router.post("/{animation_id}/drafts", status_code=status.HTTP_201_CREATED, response_model=AnimationVersionDetail,
             summary="New draft, optionally copying the spec of an existing (e.g. published) version")
def create_draft(animation_id: uuid.UUID, body: AnimationDraftCreate, response: Response,
                 session: Session = Depends(get_session),
                 who: Principal = Depends(require("asset.create"))) -> AnimationVersionDetail:
    a = svc.get_animation(session, animation_id, lock=True)
    v = svc.new_draft(session, a, based_on_version_id=body.based_on_version_id, spec=body.spec,
                      created_by=who.user_id)
    audit(session, who, "animation.draft_created", "animation_version", v.id,
          based_on=str(body.based_on_version_id) if body.based_on_version_id else None)
    session.commit()
    session.refresh(v)
    response.headers["ETag"] = _etag(v)
    return _detail(v)


@router.patch("/{animation_id}/drafts/{draft_id}", response_model=AnimationVersionDetail,
              summary="Edit a draft's spec")
def edit_draft(animation_id: uuid.UUID, draft_id: uuid.UUID, body: AnimationDraftPatch, response: Response,
               session: Session = Depends(get_session), if_match: str | None = Header(default=None),
               who: Principal = Depends(require("asset.edit_draft"))) -> AnimationVersionDetail:
    v = _draft(session, animation_id, draft_id)
    if if_match is not None and if_match != _etag(v):
        raise ApiError(412, "stale_draft", "the draft changed since you read it; reload and try again")
    svc.update_draft(session, v, body.spec)
    audit(session, who, "animation.draft_edited", "animation_version", v.id, spec_digest=v.spec_digest)
    session.commit()
    session.refresh(v)
    response.headers["ETag"] = _etag(v)
    return _detail(v)


@router.post("/{animation_id}/drafts/{draft_id}/generate", status_code=status.HTTP_202_ACCEPTED,
             response_model=AnimationVersionJobOut, summary="Build the draft in Blender and play-check it on the reference bodies (job)")
def generate(animation_id: uuid.UUID, draft_id: uuid.UUID, response: Response,
             body: AnimationGenerateBody | None = None, session: Session = Depends(get_session),
             idempotency_key: str | None = Header(default=None, max_length=200),
             who: Principal = Depends(require("asset.create"))) -> AnimationVersionJobOut:
    v = _draft(session, animation_id, draft_id)
    params = {"spec": v.spec, "previews": (body or AnimationGenerateBody()).previews.model_dump(),
              "draft_version_id": str(v.id)}
    svc.check_source_available(session, v)
    job, created = submit_and_dispatch(session, who, "generate_animation", params, idempotency_key,
                                       before_commit=lambda job: svc.begin_generate(v, job.id))
    if not created:
        response.status_code = status.HTTP_200_OK
    session.refresh(v)
    return AnimationVersionJobOut(version=_detail(v), job=job_out(job))


@router.post("/{animation_id}/drafts/{draft_id}/submit", response_model=AnimationVersionDetail,
             summary="Send a built draft whose play check passed to review")
def submit(animation_id: uuid.UUID, draft_id: uuid.UUID, session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.submit"))) -> AnimationVersionDetail:
    v = _draft(session, animation_id, draft_id)
    svc.submit(session, v)
    audit(session, who, "animation.submitted", "animation_version", v.id)
    session.commit()
    return _detail(v)


@router.delete("/{animation_id}/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Discard a draft")
def discard(animation_id: uuid.UUID, draft_id: uuid.UUID, session: Session = Depends(get_session),
            who: Principal = Depends(require("asset.edit_draft"))) -> Response:
    v = _draft(session, animation_id, draft_id)
    vid = v.id
    svc.discard_draft(session, v)
    audit(session, who, "animation.draft_discarded", "animation_version", vid)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
