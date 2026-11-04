"""Outfit Library endpoints (SRS 7): identity, drafts, builds, review, publish, and the
compatibility registry.

An outfit is its own asset; nothing here copies or changes a character. Fitting an outfit
version to a character version is a ``fit_outfit`` job whose report becomes that pair's
compatibility record; a reviewer can approve a verified fit for production.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.api.v1.jobs import _out as job_out
from app.api.v1.jobs import submit_and_dispatch
from app.api.v1.library import FileOut, _tags
from app.api.v1.schemas import AssetFileOut, DownloadOut, JobOut, Page
from app.assets import outfits as svc
from app.core.auth import Principal, audit, require
from app.core.database import get_session
from app.core.errors import ApiError
from app.core.storage import Storage
from app.models.assets import AssetVersion
from app.models.characters import CharacterVersion
from app.models.jobs import Job
from app.models.outfits import Outfit, OutfitCompatibility, OutfitVersion
from studio_contracts.jobs import OutfitPreviewSettings

router = APIRouter(prefix="/outfits", tags=["outfit library"])
wardrobe_router = APIRouter(tags=["outfit library"])


# ---------------------------------------------------------------- bodies
class OutfitCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    spec: dict[str, Any] = Field(description="OutfitSpecification; its name defaults to the outfit's")


class OutfitPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=20)
    recommended_version_id: uuid.UUID | None = None


class OutfitDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    based_on_version_id: uuid.UUID | None = None
    spec: dict[str, Any] | None = None


class OutfitDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: dict[str, Any]


class OutfitGenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previews: OutfitPreviewSettings = Field(default_factory=OutfitPreviewSettings)


class OutfitFitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_version_id: uuid.UUID
    previews: OutfitPreviewSettings = Field(default_factory=OutfitPreviewSettings)


class OutfitReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    notes: str = Field(default="", max_length=4000)


class OutfitFitDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "revoke"]
    notes: str = Field(default="", max_length=4000)


class OutfitPublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bump: Literal["minor", "major"] = Field(
        default="minor", description="major is forced when the rig or style changed, or a fit profile was dropped")


class OutfitVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    outfit_id: uuid.UUID
    status: str
    version_label: str | None
    based_on_version_id: uuid.UUID | None
    spec_digest: str
    built_digest: str | None
    fit_profiles: list[str]
    primary_fit_profile: str | None
    slots: list[str]
    review_decision: str | None
    published_at: datetime | None
    deprecated_at: datetime | None
    created_at: datetime | None = None
    created_by: uuid.UUID | None = None
    fit_ok: bool | None = None


class OutfitVersionDetail(OutfitVersionSummary):
    spec: dict[str, Any]
    style_profile_ref: str | None
    rig_profile_ref: str | None
    catalog_ref: str | None
    blender_version: str | None
    fit_report: dict[str, Any] | None
    generate_job_id: uuid.UUID | None
    review_notes: str
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    manifest_key: str | None
    manifest_sha256: str | None
    published_by: uuid.UUID | None
    files: list[FileOut] = []
    ready_for_review: list[str] = Field(default_factory=list, description="what blocks submit; empty when ready")


class OutfitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    description: str
    tags: list[str]
    scope: str
    owner_character_id: uuid.UUID | None
    lifecycle: str
    recommended_version_id: uuid.UUID | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    recommended_version: str | None = None
    latest_published: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    thumbnail_url: str | None = None


class OutfitDetail(OutfitOut):
    versions: list[OutfitVersionSummary] = []


class OutfitVersionJobOut(BaseModel):
    version: OutfitVersionDetail
    job: JobOut


class FitParty(BaseModel):
    """One side of a compatibility record: the character or the outfit, at its version."""
    id: uuid.UUID
    code: str
    name: str | None
    version_id: uuid.UUID
    version: str | None
    status: str


class FitFile(BaseModel):
    name: str
    role: str
    sha256: str
    size_bytes: int
    url: str


class FitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    outfit_version_id: uuid.UUID
    character_version_id: uuid.UUID
    status: str
    fit_job_id: uuid.UUID | None
    fit_profile: str | None
    fit_profile_declared: bool | None
    outfit_blend_sha256: str | None
    character_blend_sha256: str | None
    checked_at: datetime | None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    notes: str
    failed: list[str]
    current: bool = Field(description="the record describes the outfit version's current outfit.blend")
    character: FitParty
    outfit: FitParty
    files: list[FitFile] = Field(description="report, thumbnail, pose sheet and try-on GLB, with presigned URLs")


class FitDetail(FitOut):
    report: dict[str, Any] | None = None


class FitJobOut(BaseModel):
    fit: FitOut
    job: JobOut


# ---------------------------------------------------------------- helpers
def _summary(v: OutfitVersion, cls=OutfitVersionSummary) -> Any:
    out = cls.model_validate(v, from_attributes=True)
    out.created_at = v.entry.created_at
    out.created_by = v.entry.created_by
    out.fit_ok = (v.fit_report or {}).get("ok") if v.fit_report is not None else None
    return out


def _detail(session: Session, v: OutfitVersion) -> OutfitVersionDetail:
    out = _summary(v, OutfitVersionDetail)
    out.files = [FileOut(**AssetFileOut.model_validate(f).model_dump(), name=f.storage_key.rsplit("/", 1)[-1])
                 for f in sorted(v.entry.files, key=lambda f: f.storage_key)]
    out.ready_for_review = svc.readiness(session, v) if v.status == "draft" else []
    return out


def _thumbnail(o: Outfit) -> str | None:
    by_id = {v.id: v for v in o.versions}
    order = ([by_id[o.recommended_version_id]] if o.recommended_version_id in by_id else []) + sorted(
        o.versions, key=lambda v: (v.status == "published", v.id), reverse=True)
    for v in order:
        thumb = next((f for f in v.entry.files if f.role == "thumbnail"), None)
        if thumb is not None:
            return Storage.for_bucket(svc.bucket_for(v)).presign_get(thumb.storage_key)
    return None


def _outfit(o: Outfit, with_versions: bool = False) -> OutfitOut:
    cls = OutfitDetail if with_versions else OutfitOut
    out = cls.model_validate(o, from_attributes=True)
    counts: dict[str, int] = {}
    for v in o.versions:
        counts[v.status] = counts.get(v.status, 0) + 1
    out.counts = counts
    published = [v for v in o.versions if v.status == "published"]
    if published:
        out.latest_published = max(published, key=lambda v: (v.entry.major, v.entry.minor)).version_label
    rec = next((v for v in o.versions if v.id == o.recommended_version_id), None)
    out.recommended_version = rec.version_label if rec else None
    out.thumbnail_url = _thumbnail(o)
    if with_versions:
        out.versions = [_summary(v) for v in sorted(o.versions, key=lambda v: v.id, reverse=True)]
    return out


def _etag(v: OutfitVersion) -> str:
    return f'"{v.spec_digest[:16]}-{int(v.updated_at.timestamp() * 1_000_000)}"'


def _draft(session: Session, outfit_id: uuid.UUID, draft_id: uuid.UUID) -> OutfitVersion:
    v = svc.get_version(session, outfit_id, draft_id, lock=True)
    if v.status != "draft":
        raise ApiError(409, "not_a_draft", f"version is {v.status}; published versions never change, "
                       "create a draft based on it instead", version_status=v.status)
    return v


def _fit(session: Session, row: OutfitCompatibility, cls=FitOut) -> Any:
    ov = session.get(OutfitVersion, row.outfit_version_id)
    cv = session.get(CharacterVersion, row.character_version_id)
    blend = svc.blend_file(ov)
    store = Storage.for_bucket("work")
    fields = {k: getattr(row, k) for k in ("id", "outfit_version_id", "character_version_id", "status", "fit_job_id",
                                           "fit_profile", "fit_profile_declared", "outfit_blend_sha256",
                                           "character_blend_sha256", "checked_at", "decided_by", "decided_at", "notes")}
    if cls is FitDetail:
        fields["report"] = row.report
    return cls(
        **fields,
        current=row.outfit_blend_sha256 is None or (blend is not None and blend.sha256 == row.outfit_blend_sha256),
        failed=list((row.report or {}).get("failed", [])),
        character=FitParty(id=cv.character_id, code=cv.entry.display_code, name=cv.spec.get("name"), version_id=cv.id,
                           version=cv.version_label, status=cv.status),
        outfit=FitParty(id=ov.outfit_id, code=ov.entry.display_code, name=ov.spec.get("name"), version_id=ov.id,
                        version=ov.version_label, status=ov.status),
        files=[FitFile(name=f["storage_key"].rsplit("/", 1)[-1], role=f["role"], sha256=f["sha256"],
                       size_bytes=f["size_bytes"], url=store.presign_get(f["storage_key"])) for f in row.files or []])


# ---------------------------------------------------------------- identity
@router.post("", status_code=status.HTTP_201_CREATED, response_model=OutfitDetail,
             summary="Create an outfit and its first draft from a specification")
def create(body: OutfitCreate, session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.create"))) -> OutfitDetail:
    o, v = svc.create_outfit(session, name=body.name, description=body.description, tags=_tags(body.tags),
                             spec=body.spec, created_by=who.user_id)
    audit(session, who, "outfit.created", "outfit", o.id, code=o.code, draft=str(v.id), scope=o.scope)
    session.commit()
    session.refresh(o)
    return _outfit(o, with_versions=True)


@router.get("", response_model=Page[OutfitOut], summary="Gallery: search and filter outfits")
def gallery(session: Session = Depends(get_session), q: str | None = Query(default=None, max_length=80),
            tag: list[str] = Query(default_factory=list), scope: str | None = None,
            owner_character_id: uuid.UUID | None = None, status_: str | None = Query(default=None, alias="status"),
            fit_profile: str | None = None, slot: str | None = None, limit: int = Query(default=48, ge=1, le=200),
            cursor: uuid.UUID | None = None, _: Principal = Depends(require("asset.read"))) -> Page[OutfitOut]:
    query = select(Outfit).order_by(Outfit.id.desc()).limit(limit + 1)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        query = query.where(or_(Outfit.name.ilike(like), Outfit.code.ilike(like), Outfit.description.ilike(like)))
    if tag:
        query = query.where(Outfit.tags.contains(tag))
    if scope:
        query = query.where(Outfit.scope == scope)
    if owner_character_id:
        query = query.where(Outfit.owner_character_id == owner_character_id)
    if status_ or fit_profile or slot:
        sub = select(OutfitVersion.id).join(AssetVersion, AssetVersion.id == OutfitVersion.id).where(
            OutfitVersion.outfit_id == Outfit.id)
        if status_:
            sub = sub.where(AssetVersion.status == status_)
        if fit_profile:
            sub = sub.where(OutfitVersion.fit_profiles.contains([fit_profile]))
        if slot:
            sub = sub.where(OutfitVersion.slots.contains([slot]))
        query = query.where(exists(sub))
    if cursor:
        query = query.where(Outfit.id < cursor)
    rows = list(session.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    return Page(items=[_outfit(o) for o in rows], next_cursor=str(rows[-1].id) if more else None)


@router.get("/{outfit_id}", response_model=OutfitDetail)
def detail(outfit_id: uuid.UUID, session: Session = Depends(get_session),
           _: Principal = Depends(require("asset.read"))) -> OutfitDetail:
    return _outfit(svc.get_outfit(session, outfit_id), with_versions=True)


@router.patch("/{outfit_id}", response_model=OutfitDetail, summary="Name, description, tags, recommended version")
def patch_identity(outfit_id: uuid.UUID, body: OutfitPatch, session: Session = Depends(get_session),
                   who: Principal = Depends(require("asset.edit_identity"))) -> OutfitDetail:
    o = svc.get_outfit(session, outfit_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    if "recommended_version_id" in changes:
        rid = changes["recommended_version_id"]
        if rid is not None:
            v = svc.get_version(session, o.id, rid)
            if v.status != "published":
                raise ApiError(409, "not_published", "only a published version can be recommended", version_status=v.status)
        o.recommended_version_id = rid
    if "tags" in changes:
        o.tags = _tags(changes["tags"] or [])
    for field in ("name", "description"):
        if changes.get(field) is not None:
            setattr(o, field, changes[field])
    audit(session, who, "outfit.updated", "outfit", o.id,
          **{k: str(v) if isinstance(v, uuid.UUID) else v for k, v in changes.items()})
    session.commit()
    session.refresh(o)
    return _outfit(o, with_versions=True)


# ---------------------------------------------------------------- versions
@router.get("/{outfit_id}/versions", response_model=list[OutfitVersionSummary], summary="Version history, newest first")
def history(outfit_id: uuid.UUID, session: Session = Depends(get_session),
            _: Principal = Depends(require("asset.read"))) -> list[OutfitVersionSummary]:
    o = svc.get_outfit(session, outfit_id)
    return [_summary(v) for v in sorted(o.versions, key=lambda v: v.id, reverse=True)]


@router.get("/{outfit_id}/versions/{version_id}", response_model=OutfitVersionDetail)
def version_detail(outfit_id: uuid.UUID, version_id: uuid.UUID, response: Response,
                   session: Session = Depends(get_session),
                   _: Principal = Depends(require("asset.read"))) -> OutfitVersionDetail:
    v = svc.get_version(session, outfit_id, version_id)
    if v.status == "draft":
        response.headers["ETag"] = _etag(v)
    return _detail(session, v)


@router.get("/{outfit_id}/versions/{version_id}/files/{name}", summary="A version file: 302 to a presigned URL",
            responses={302: {"description": "redirect to the object"}, 200: {"model": DownloadOut}})
def version_file(outfit_id: uuid.UUID, version_id: uuid.UUID, name: str, redirect: bool = True,
                 session: Session = Depends(get_session), _: Principal = Depends(require("asset.read"))):
    v = svc.get_version(session, outfit_id, version_id)
    f = svc.files_by_name(v).get(name)
    if f is None:
        raise ApiError(404, "file_not_found", f"version has no file {name!r}")
    store = Storage.for_bucket(svc.bucket_for(v))
    url = store.presign_get(f.storage_key, filename=name)
    if redirect:
        return RedirectResponse(url, status_code=302)
    return DownloadOut(url=url, expires_in_s=store.presign_expiry_s)


@router.post("/{outfit_id}/versions/{version_id}/review", response_model=OutfitVersionDetail,
             summary="Approve, or reject back to draft with notes")
def review(outfit_id: uuid.UUID, version_id: uuid.UUID, body: OutfitReviewBody, session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.review"))) -> OutfitVersionDetail:
    v = svc.get_version(session, outfit_id, version_id, lock=True)
    svc.review(session, v, decision=body.decision, notes=body.notes, reviewer=who.user_id)
    audit(session, who, f"outfit.review.{body.decision}", "outfit_version", v.id, notes=body.notes)
    session.commit()
    return _detail(session, v)


@router.post("/{outfit_id}/versions/{version_id}/publish", status_code=status.HTTP_202_ACCEPTED,
             response_model=OutfitVersionJobOut, summary="Publish an approved version (job on the publisher queue)")
def publish(outfit_id: uuid.UUID, version_id: uuid.UUID, response: Response, body: OutfitPublishBody | None = None,
            session: Session = Depends(get_session), idempotency_key: str | None = Header(default=None, max_length=200),
            who: Principal = Depends(require("asset.publish"))) -> OutfitVersionJobOut:
    v = svc.get_version(session, outfit_id, version_id, lock=True)
    params = {"version_id": str(v.id), "bump": (body or OutfitPublishBody()).bump}
    replay = idempotency_key and session.scalar(select(Job.id).where(Job.idempotency_key == idempotency_key))
    if not replay:
        svc.check_publishable(v)
    job, created = submit_and_dispatch(session, who, "publish_outfit", params, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    session.refresh(v)
    return OutfitVersionJobOut(version=_detail(session, v), job=job_out(job))


@router.post("/{outfit_id}/versions/{version_id}/deprecate", response_model=OutfitVersionDetail,
             summary="Hide a published version from pickers; it stays loadable")
def deprecate(outfit_id: uuid.UUID, version_id: uuid.UUID, session: Session = Depends(get_session),
              who: Principal = Depends(require("asset.publish"))) -> OutfitVersionDetail:
    svc.get_outfit(session, outfit_id, lock=True)
    v = svc.get_version(session, outfit_id, version_id, lock=True)
    svc.deprecate(session, v)
    audit(session, who, "outfit.deprecated", "outfit_version", v.id, version=v.version_label)
    session.commit()
    return _detail(session, v)


# ---------------------------------------------------------------- drafts
@router.post("/{outfit_id}/drafts", status_code=status.HTTP_201_CREATED, response_model=OutfitVersionDetail,
             summary="New draft, optionally copying the spec of an existing (e.g. published) version")
def create_draft(outfit_id: uuid.UUID, body: OutfitDraftCreate, response: Response,
                 session: Session = Depends(get_session),
                 who: Principal = Depends(require("asset.create"))) -> OutfitVersionDetail:
    o = svc.get_outfit(session, outfit_id, lock=True)
    v = svc.new_draft(session, o, based_on_version_id=body.based_on_version_id, spec=body.spec,
                      created_by=who.user_id)
    audit(session, who, "outfit.draft_created", "outfit_version", v.id,
          based_on=str(body.based_on_version_id) if body.based_on_version_id else None)
    session.commit()
    session.refresh(v)
    response.headers["ETag"] = _etag(v)
    return _detail(session, v)


@router.patch("/{outfit_id}/drafts/{draft_id}", response_model=OutfitVersionDetail, summary="Edit a draft's spec")
def edit_draft(outfit_id: uuid.UUID, draft_id: uuid.UUID, body: OutfitDraftPatch, response: Response,
               session: Session = Depends(get_session), if_match: str | None = Header(default=None),
               who: Principal = Depends(require("asset.edit_draft"))) -> OutfitVersionDetail:
    v = _draft(session, outfit_id, draft_id)
    if if_match is not None and if_match != _etag(v):
        raise ApiError(412, "stale_draft", "the draft changed since you read it; reload and try again")
    svc.update_draft(session, v, body.spec)
    audit(session, who, "outfit.draft_edited", "outfit_version", v.id, spec_digest=v.spec_digest)
    session.commit()
    session.refresh(v)
    response.headers["ETag"] = _etag(v)
    return _detail(session, v)


@router.post("/{outfit_id}/drafts/{draft_id}/generate", status_code=status.HTTP_202_ACCEPTED,
             response_model=OutfitVersionJobOut, summary="Build the draft in Blender and fit-check it (job)")
def generate(outfit_id: uuid.UUID, draft_id: uuid.UUID, response: Response, body: OutfitGenerateBody | None = None,
             session: Session = Depends(get_session), idempotency_key: str | None = Header(default=None, max_length=200),
             who: Principal = Depends(require("asset.create"))) -> OutfitVersionJobOut:
    v = _draft(session, outfit_id, draft_id)
    params = {"spec": v.spec, "previews": (body or OutfitGenerateBody()).previews.model_dump(),
              "draft_version_id": str(v.id)}
    job, created = submit_and_dispatch(session, who, "generate_outfit", params, idempotency_key,
                                       before_commit=lambda job: svc.begin_generate(v, job.id))
    if not created:
        response.status_code = status.HTTP_200_OK
    session.refresh(v)
    return OutfitVersionJobOut(version=_detail(session, v), job=job_out(job))


@router.post("/{outfit_id}/drafts/{draft_id}/submit", response_model=OutfitVersionDetail,
             summary="Send a built draft whose fit check passed to review")
def submit(outfit_id: uuid.UUID, draft_id: uuid.UUID, session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.submit"))) -> OutfitVersionDetail:
    v = _draft(session, outfit_id, draft_id)
    svc.submit(session, v)
    audit(session, who, "outfit.submitted", "outfit_version", v.id)
    session.commit()
    return _detail(session, v)


@router.delete("/{outfit_id}/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Discard a draft")
def discard(outfit_id: uuid.UUID, draft_id: uuid.UUID, session: Session = Depends(get_session),
            who: Principal = Depends(require("asset.edit_draft"))) -> Response:
    v = _draft(session, outfit_id, draft_id)
    vid = v.id
    svc.discard_draft(session, v)
    audit(session, who, "outfit.draft_discarded", "outfit_version", vid)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- compatibility registry
@router.post("/{outfit_id}/versions/{version_id}/fits", status_code=status.HTTP_202_ACCEPTED, response_model=FitJobOut,
             summary="Fit-check this outfit version on a published character version (job)")
def request_fit(outfit_id: uuid.UUID, version_id: uuid.UUID, body: OutfitFitBody, response: Response,
                session: Session = Depends(get_session),
                idempotency_key: str | None = Header(default=None, max_length=200),
                who: Principal = Depends(require("asset.create"))) -> FitJobOut:
    svc.get_outfit(session, outfit_id, lock=True)
    v = svc.get_version(session, outfit_id, version_id)
    cv = svc.check_fittable(session, v, body.character_version_id)
    params = {"outfit_version_id": str(v.id), "character_version_id": str(cv.id),
              "previews": body.previews.model_dump()}
    holder: dict[str, OutfitCompatibility] = {}
    job, created = submit_and_dispatch(session, who, "fit_outfit", params, idempotency_key,
                                       before_commit=lambda job: holder.setdefault("row", svc.begin_fit(session, v, cv, job.id)))
    if not created:
        response.status_code = status.HTTP_200_OK
    row = svc.get_fit(session, v.id, cv.id)
    return FitJobOut(fit=_fit(session, row), job=job_out(job))


@router.get("/{outfit_id}/versions/{version_id}/fits", response_model=list[FitOut],
            summary="Compatibility records of this outfit version")
def list_fits(outfit_id: uuid.UUID, version_id: uuid.UUID, session: Session = Depends(get_session),
              _: Principal = Depends(require("asset.read"))) -> list[FitOut]:
    v = svc.get_version(session, outfit_id, version_id)
    rows = session.scalars(select(OutfitCompatibility).where(OutfitCompatibility.outfit_version_id == v.id)
                           .order_by(OutfitCompatibility.created_at))
    return [_fit(session, r) for r in rows]


@router.get("/{outfit_id}/versions/{version_id}/fits/{character_version_id}", response_model=FitDetail,
            summary="One compatibility record with its full fit report")
def fit_detail(outfit_id: uuid.UUID, version_id: uuid.UUID, character_version_id: uuid.UUID,
               session: Session = Depends(get_session), _: Principal = Depends(require("asset.read"))) -> FitDetail:
    v = svc.get_version(session, outfit_id, version_id)
    row = svc.get_fit(session, v.id, character_version_id)
    if row is None:
        raise ApiError(404, "fit_not_found", "no fit check for this pair")
    return _fit(session, row, FitDetail)


@router.post("/{outfit_id}/versions/{version_id}/fits/{character_version_id}/decision", response_model=FitOut,
             summary="Approve a verified fit for production, or revoke a fit")
def decide_fit(outfit_id: uuid.UUID, version_id: uuid.UUID, character_version_id: uuid.UUID, body: OutfitFitDecision,
               session: Session = Depends(get_session), who: Principal = Depends(require("asset.review"))) -> FitOut:
    v = svc.get_version(session, outfit_id, version_id)
    row = svc.get_fit(session, v.id, character_version_id, lock=True)
    if row is None:
        raise ApiError(404, "fit_not_found", "no fit check for this pair")
    svc.decide_fit(session, row, decision=body.decision, notes=body.notes, who=who.user_id)
    audit(session, who, f"outfit.fit.{body.decision}", "outfit_compatibility", row.id,
          outfit_version=str(v.id), character_version=str(character_version_id), notes=body.notes)
    session.commit()
    return _fit(session, row)


@wardrobe_router.get("/characters/{character_id}/versions/{character_version_id}/outfits", response_model=list[FitOut],
                     summary="Wardrobe: every outfit version checked on this character version")
def wardrobe(character_id: uuid.UUID, character_version_id: uuid.UUID, status_: list[str] = Query(
        default_factory=list, alias="status"), session: Session = Depends(get_session),
        _: Principal = Depends(require("asset.read"))) -> list[FitOut]:
    cv = session.get(CharacterVersion, character_version_id)
    if cv is None or cv.character_id != character_id:
        raise ApiError(404, "character_version_not_found", str(character_version_id))
    q = select(OutfitCompatibility).where(OutfitCompatibility.character_version_id == cv.id)
    if status_:
        q = q.where(OutfitCompatibility.status.in_(status_))
    return [_fit(session, r) for r in session.scalars(q.order_by(OutfitCompatibility.created_at))]
