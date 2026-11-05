"""Accessory Library endpoints (SRS 8): the catalog, validation, identity, drafts, builds,
review and publish.

An accessory is its own asset; nothing here touches a character or an outfit. Where it may
go is its compatibility metadata (rig profile, attachment points, body-fit profiles); a scene
checks that metadata when it pins the accessory, and the attach check on the actual
character runs when the scene is assembled.
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
from studio_contracts.accessory import (
    ACCESSORIES,
    AccessoryConventions,
    AccessoryDef,
    AccessorySpecification,
    ResolvedAccessory,
    get_accessory_conventions,
    resolve_accessory,
    validate_accessory,
)
from studio_contracts.character import Issue
from studio_contracts.jobs import AccessoryPreviewSettings

from app.api.v1.characters import _pydantic_issues
from app.api.v1.jobs import _out as job_out
from app.api.v1.jobs import submit_and_dispatch
from app.api.v1.library import FileOut, _tags
from app.api.v1.schemas import AssetFileOut, DownloadOut, JobOut, Page
from app.assets import accessories as svc
from app.core.auth import Principal, audit, require
from app.core.database import get_session
from app.core.errors import ApiError
from app.core.storage import Storage
from app.models.accessories import Accessory, AccessoryVersion
from app.models.assets import AssetVersion
from app.models.jobs import Job

router = APIRouter(prefix="/accessories", tags=["accessory library"])
catalog_router = APIRouter(tags=["accessory library"])


# ---------------------------------------------------------------- catalog and validation
class AccessoryValidationOut(BaseModel):
    valid: bool
    issues: list[Issue]
    resolved: ResolvedAccessory | None = None
    digest: str | None = None


@catalog_router.get("/accessory-items", response_model=list[AccessoryDef],
                    summary="The accessory catalog (procedural stand-ins)")
def list_items(_: Principal = Depends(require("asset.read"))) -> list[AccessoryDef]:
    return list(ACCESSORIES)


@catalog_router.get("/style-profiles/{ref}/accessories", response_model=AccessoryConventions,
                    summary="Finishes, attach thresholds and the pose suite of a style")
def get_conventions(ref: str, _: Principal = Depends(require("asset.read"))) -> AccessoryConventions:
    try:
        return get_accessory_conventions(ref)
    except KeyError as exc:
        raise ApiError(404, "accessory_conventions_not_found", str(exc.args[0])) from None


@router.post("/spec/validate", response_model=AccessoryValidationOut,
             summary="Validate an accessory specification against the catalog and its style")
def validate(body: dict[str, Any], _: Principal = Depends(require("asset.read"))) -> AccessoryValidationOut:
    """Always 200: an invalid spec is a normal answer here, listing every issue."""
    try:
        spec = AccessorySpecification.model_validate(body)
    except ValidationError as exc:
        return AccessoryValidationOut(valid=False, issues=_pydantic_issues(exc))
    issues = validate_accessory(spec)
    if any(i.severity == "error" for i in issues):
        return AccessoryValidationOut(valid=False, issues=issues)
    resolved = resolve_accessory(spec)
    return AccessoryValidationOut(valid=True, issues=issues, resolved=resolved, digest=resolved.digest())


# ---------------------------------------------------------------- bodies
class AccessoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    spec: dict[str, Any] = Field(description="AccessorySpecification; its name defaults to the accessory's")


class AccessoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=20)
    recommended_version_id: uuid.UUID | None = None


class AccessoryDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    based_on_version_id: uuid.UUID | None = None
    spec: dict[str, Any] | None = None


class AccessoryDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: dict[str, Any]


class AccessoryGenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previews: AccessoryPreviewSettings = Field(default_factory=AccessoryPreviewSettings)


class AccessoryReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    notes: str = Field(default="", max_length=4000)


class AccessoryPublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bump: Literal["minor", "major"] = Field(
        default="minor", description="major is forced when compatibility narrows (rig, style, points, fit profiles)")


class AccessoryVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    accessory_id: uuid.UUID
    status: str
    version_label: str | None
    based_on_version_id: uuid.UUID | None
    spec_digest: str
    built_digest: str | None
    item_ref: str | None
    attach_point: str | None
    attach_points: list[str]
    fit_profiles: list[str]
    review_decision: str | None
    published_at: datetime | None
    deprecated_at: datetime | None
    created_at: datetime | None = None
    created_by: uuid.UUID | None = None
    check_ok: bool | None = None


class AccessoryVersionDetail(AccessoryVersionSummary):
    spec: dict[str, Any]
    style_profile_ref: str | None
    rig_profile_ref: str | None
    catalog_ref: str | None
    conflicts_with_slots: list[str]
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


class AccessoryOut(BaseModel):
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


class AccessoryDetail(AccessoryOut):
    versions: list[AccessoryVersionSummary] = []


class AccessoryVersionJobOut(BaseModel):
    version: AccessoryVersionDetail
    job: JobOut


# ---------------------------------------------------------------- helpers
def _summary(v: AccessoryVersion, cls=AccessoryVersionSummary) -> Any:
    out = cls.model_validate(v, from_attributes=True)
    out.created_at = v.entry.created_at
    out.created_by = v.entry.created_by
    out.check_ok = (v.check_report or {}).get("ok") if v.check_report is not None else None
    return out


def _detail(v: AccessoryVersion) -> AccessoryVersionDetail:
    out = _summary(v, AccessoryVersionDetail)
    out.files = [FileOut(**AssetFileOut.model_validate(f).model_dump(), name=f.storage_key.rsplit("/", 1)[-1])
                 for f in sorted(v.entry.files, key=lambda f: f.storage_key)]
    out.ready_for_review = svc.readiness(v) if v.status == "draft" else []
    return out


def _thumbnail(a: Accessory) -> str | None:
    by_id = {v.id: v for v in a.versions}
    order = ([by_id[a.recommended_version_id]] if a.recommended_version_id in by_id else []) + sorted(
        a.versions, key=lambda v: (v.status == "published", v.id), reverse=True)
    for v in order:
        thumb = next((f for f in v.entry.files if f.role == "thumbnail"), None)
        if thumb is not None:
            return Storage.for_bucket(svc.bucket_for(v)).presign_get(thumb.storage_key)
    return None


def _accessory(a: Accessory, with_versions: bool = False) -> AccessoryOut:
    cls = AccessoryDetail if with_versions else AccessoryOut
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


def _etag(v: AccessoryVersion) -> str:
    return f'"{v.spec_digest[:16]}-{int(v.updated_at.timestamp() * 1_000_000)}"'


def _draft(session: Session, accessory_id: uuid.UUID, draft_id: uuid.UUID) -> AccessoryVersion:
    v = svc.get_version(session, accessory_id, draft_id, lock=True)
    if v.status != "draft":
        raise ApiError(409, "not_a_draft", f"version is {v.status}; published versions never change, "
                       "create a draft based on it instead", version_status=v.status)
    return v


# ---------------------------------------------------------------- identity
@router.post("", status_code=status.HTTP_201_CREATED, response_model=AccessoryDetail,
             summary="Create an accessory and its first draft from a specification")
def create(body: AccessoryCreate, session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.create"))) -> AccessoryDetail:
    a, v = svc.create_accessory(session, name=body.name, description=body.description, tags=_tags(body.tags),
                                spec=body.spec, created_by=who.user_id)
    audit(session, who, "accessory.created", "accessory", a.id, code=a.code, draft=str(v.id), category=a.category)
    session.commit()
    session.refresh(a)
    return _accessory(a, with_versions=True)


@router.get("", response_model=Page[AccessoryOut], summary="Gallery: search and filter accessories")
def gallery(session: Session = Depends(get_session), q: str | None = Query(default=None, max_length=80),
            tag: list[str] = Query(default_factory=list), category: str | None = None,
            status_: str | None = Query(default=None, alias="status"), attach_point: str | None = None,
            fit_profile: str | None = Query(default=None, description="declared for this profile (or for all)"),
            limit: int = Query(default=48, ge=1, le=200), cursor: uuid.UUID | None = None,
            _: Principal = Depends(require("asset.read"))) -> Page[AccessoryOut]:
    query = select(Accessory).order_by(Accessory.id.desc()).limit(limit + 1)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        query = query.where(or_(Accessory.name.ilike(like), Accessory.code.ilike(like),
                                Accessory.description.ilike(like)))
    if tag:
        query = query.where(Accessory.tags.contains(tag))
    if category:
        query = query.where(Accessory.category == category)
    if status_ or attach_point or fit_profile:
        sub = select(AccessoryVersion.id).join(AssetVersion, AssetVersion.id == AccessoryVersion.id).where(
            AccessoryVersion.accessory_id == Accessory.id)
        if status_:
            sub = sub.where(AssetVersion.status == status_)
        if attach_point:
            sub = sub.where(AccessoryVersion.attach_points.contains([attach_point]))
        if fit_profile:
            sub = sub.where(or_(AccessoryVersion.fit_profiles.contains([fit_profile]),
                                AccessoryVersion.fit_profiles == []))
        query = query.where(exists(sub))
    if cursor:
        query = query.where(Accessory.id < cursor)
    rows = list(session.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    return Page(items=[_accessory(a) for a in rows], next_cursor=str(rows[-1].id) if more else None)


@router.get("/{accessory_id}", response_model=AccessoryDetail)
def detail(accessory_id: uuid.UUID, session: Session = Depends(get_session),
           _: Principal = Depends(require("asset.read"))) -> AccessoryDetail:
    return _accessory(svc.get_accessory(session, accessory_id), with_versions=True)


@router.patch("/{accessory_id}", response_model=AccessoryDetail,
              summary="Name, description, tags, recommended version")
def patch_identity(accessory_id: uuid.UUID, body: AccessoryPatch, session: Session = Depends(get_session),
                   who: Principal = Depends(require("asset.edit_identity"))) -> AccessoryDetail:
    a = svc.get_accessory(session, accessory_id, lock=True)
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
    audit(session, who, "accessory.updated", "accessory", a.id,
          **{k: str(v) if isinstance(v, uuid.UUID) else v for k, v in changes.items()})
    session.commit()
    session.refresh(a)
    return _accessory(a, with_versions=True)


# ---------------------------------------------------------------- versions
@router.get("/{accessory_id}/versions", response_model=list[AccessoryVersionSummary],
            summary="Version history, newest first")
def history(accessory_id: uuid.UUID, session: Session = Depends(get_session),
            _: Principal = Depends(require("asset.read"))) -> list[AccessoryVersionSummary]:
    a = svc.get_accessory(session, accessory_id)
    return [_summary(v) for v in sorted(a.versions, key=lambda v: v.id, reverse=True)]


@router.get("/{accessory_id}/versions/{version_id}", response_model=AccessoryVersionDetail)
def version_detail(accessory_id: uuid.UUID, version_id: uuid.UUID, response: Response,
                   session: Session = Depends(get_session),
                   _: Principal = Depends(require("asset.read"))) -> AccessoryVersionDetail:
    v = svc.get_version(session, accessory_id, version_id)
    if v.status == "draft":
        response.headers["ETag"] = _etag(v)
    return _detail(v)


@router.get("/{accessory_id}/versions/{version_id}/files/{name}", summary="A version file: 302 to a presigned URL",
            responses={302: {"description": "redirect to the object"}, 200: {"model": DownloadOut}})
def version_file(accessory_id: uuid.UUID, version_id: uuid.UUID, name: str, redirect: bool = True,
                 session: Session = Depends(get_session), _: Principal = Depends(require("asset.read"))):
    v = svc.get_version(session, accessory_id, version_id)
    f = svc.files_by_name(v).get(name)
    if f is None:
        raise ApiError(404, "file_not_found", f"version has no file {name!r}")
    store = Storage.for_bucket(svc.bucket_for(v))
    url = store.presign_get(f.storage_key, filename=name)
    if redirect:
        return RedirectResponse(url, status_code=302)
    return DownloadOut(url=url, expires_in_s=store.presign_expiry_s)


@router.post("/{accessory_id}/versions/{version_id}/review", response_model=AccessoryVersionDetail,
             summary="Approve, or reject back to draft with notes")
def review(accessory_id: uuid.UUID, version_id: uuid.UUID, body: AccessoryReviewBody,
           session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.review"))) -> AccessoryVersionDetail:
    v = svc.get_version(session, accessory_id, version_id, lock=True)
    svc.review(session, v, decision=body.decision, notes=body.notes, reviewer=who.user_id)
    audit(session, who, f"accessory.review.{body.decision}", "accessory_version", v.id, notes=body.notes)
    session.commit()
    return _detail(v)


@router.post("/{accessory_id}/versions/{version_id}/publish", status_code=status.HTTP_202_ACCEPTED,
             response_model=AccessoryVersionJobOut, summary="Publish an approved version (job on the publisher queue)")
def publish(accessory_id: uuid.UUID, version_id: uuid.UUID, response: Response,
            body: AccessoryPublishBody | None = None, session: Session = Depends(get_session),
            idempotency_key: str | None = Header(default=None, max_length=200),
            who: Principal = Depends(require("asset.publish"))) -> AccessoryVersionJobOut:
    v = svc.get_version(session, accessory_id, version_id, lock=True)
    params = {"version_id": str(v.id), "bump": (body or AccessoryPublishBody()).bump}
    replay = idempotency_key and session.scalar(select(Job.id).where(Job.idempotency_key == idempotency_key))
    if not replay:
        svc.check_publishable(v)
    job, created = submit_and_dispatch(session, who, "publish_accessory", params, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    session.refresh(v)
    return AccessoryVersionJobOut(version=_detail(v), job=job_out(job))


@router.post("/{accessory_id}/versions/{version_id}/deprecate", response_model=AccessoryVersionDetail,
             summary="Hide a published version from pickers; scenes that pin it still load it")
def deprecate(accessory_id: uuid.UUID, version_id: uuid.UUID, session: Session = Depends(get_session),
              who: Principal = Depends(require("asset.publish"))) -> AccessoryVersionDetail:
    svc.get_accessory(session, accessory_id, lock=True)
    v = svc.get_version(session, accessory_id, version_id, lock=True)
    svc.deprecate(session, v)
    audit(session, who, "accessory.deprecated", "accessory_version", v.id, version=v.version_label)
    session.commit()
    return _detail(v)


# ---------------------------------------------------------------- drafts
@router.post("/{accessory_id}/drafts", status_code=status.HTTP_201_CREATED, response_model=AccessoryVersionDetail,
             summary="New draft, optionally copying the spec of an existing (e.g. published) version")
def create_draft(accessory_id: uuid.UUID, body: AccessoryDraftCreate, response: Response,
                 session: Session = Depends(get_session),
                 who: Principal = Depends(require("asset.create"))) -> AccessoryVersionDetail:
    a = svc.get_accessory(session, accessory_id, lock=True)
    v = svc.new_draft(session, a, based_on_version_id=body.based_on_version_id, spec=body.spec,
                      created_by=who.user_id)
    audit(session, who, "accessory.draft_created", "accessory_version", v.id,
          based_on=str(body.based_on_version_id) if body.based_on_version_id else None)
    session.commit()
    session.refresh(v)
    response.headers["ETag"] = _etag(v)
    return _detail(v)


@router.patch("/{accessory_id}/drafts/{draft_id}", response_model=AccessoryVersionDetail,
              summary="Edit a draft's spec")
def edit_draft(accessory_id: uuid.UUID, draft_id: uuid.UUID, body: AccessoryDraftPatch, response: Response,
               session: Session = Depends(get_session), if_match: str | None = Header(default=None),
               who: Principal = Depends(require("asset.edit_draft"))) -> AccessoryVersionDetail:
    v = _draft(session, accessory_id, draft_id)
    if if_match is not None and if_match != _etag(v):
        raise ApiError(412, "stale_draft", "the draft changed since you read it; reload and try again")
    svc.update_draft(session, v, body.spec)
    audit(session, who, "accessory.draft_edited", "accessory_version", v.id, spec_digest=v.spec_digest)
    session.commit()
    session.refresh(v)
    response.headers["ETag"] = _etag(v)
    return _detail(v)


@router.post("/{accessory_id}/drafts/{draft_id}/generate", status_code=status.HTTP_202_ACCEPTED,
             response_model=AccessoryVersionJobOut, summary="Build the draft in Blender and attach-check it (job)")
def generate(accessory_id: uuid.UUID, draft_id: uuid.UUID, response: Response,
             body: AccessoryGenerateBody | None = None, session: Session = Depends(get_session),
             idempotency_key: str | None = Header(default=None, max_length=200),
             who: Principal = Depends(require("asset.create"))) -> AccessoryVersionJobOut:
    v = _draft(session, accessory_id, draft_id)
    params = {"spec": v.spec, "previews": (body or AccessoryGenerateBody()).previews.model_dump(),
              "draft_version_id": str(v.id)}
    job, created = submit_and_dispatch(session, who, "generate_accessory", params, idempotency_key,
                                       before_commit=lambda job: svc.begin_generate(v, job.id))
    if not created:
        response.status_code = status.HTTP_200_OK
    session.refresh(v)
    return AccessoryVersionJobOut(version=_detail(v), job=job_out(job))


@router.post("/{accessory_id}/drafts/{draft_id}/submit", response_model=AccessoryVersionDetail,
             summary="Send a built draft whose attach check passed to review")
def submit(accessory_id: uuid.UUID, draft_id: uuid.UUID, session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.submit"))) -> AccessoryVersionDetail:
    v = _draft(session, accessory_id, draft_id)
    svc.submit(session, v)
    audit(session, who, "accessory.submitted", "accessory_version", v.id)
    session.commit()
    return _detail(v)


@router.delete("/{accessory_id}/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Discard a draft")
def discard(accessory_id: uuid.UUID, draft_id: uuid.UUID, session: Session = Depends(get_session),
            who: Principal = Depends(require("asset.edit_draft"))) -> Response:
    v = _draft(session, accessory_id, draft_id)
    vid = v.id
    svc.discard_draft(session, v)
    audit(session, who, "accessory.draft_discarded", "accessory_version", vid)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
