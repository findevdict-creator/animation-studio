"""Prop and Environment Library endpoints (SRS 12, Phase 7): the catalogs, validation,
identity, drafts, builds, review and publish.

``/props`` and ``/environments`` are built by one router factory over ``SetService``: the
lifecycle is the same, only the metadata differs (a prop's size, solidity and surfaces; an
environment's template, lighting and layout). A scene pins published versions of either.
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
from studio_contracts.character import Issue
from studio_contracts.environment import (
    ENVIRONMENTS,
    LIGHTING,
    PROPS,
    EnvironmentDef,
    LightingPreset,
    PropDef,
    ResolvedEnvironment,
    ResolvedProp,
)
from studio_contracts.jobs import SetPreviewSettings

from app.api.v1.characters import _pydantic_issues
from app.api.v1.jobs import _out as job_out
from app.api.v1.jobs import submit_and_dispatch
from app.api.v1.library import FileOut, _tags
from app.api.v1.schemas import AssetFileOut, DownloadOut, JobOut, Page
from app.assets.sets import SetService, environments, props
from app.core.auth import Principal, audit, require
from app.core.database import get_session
from app.core.errors import ApiError
from app.core.storage import Storage
from app.models.assets import AssetVersion
from app.models.jobs import Job

catalog_router = APIRouter(tags=["environment library"])


@catalog_router.get("/prop-items", response_model=list[PropDef], summary="The prop catalog (procedural stand-ins)")
def list_prop_items(_: Principal = Depends(require("asset.read"))) -> list[PropDef]:
    return list(PROPS)


@catalog_router.get("/environment-templates", response_model=list[EnvironmentDef],
                    summary="The environment templates (procedural stand-ins)")
def list_templates(_: Principal = Depends(require("asset.read"))) -> list[EnvironmentDef]:
    return list(ENVIRONMENTS)


@catalog_router.get("/lighting-presets", response_model=list[LightingPreset], summary="Lighting presets")
def list_lighting(_: Principal = Depends(require("asset.read"))) -> list[LightingPreset]:
    return list(LIGHTING.values())


# ---------------------------------------------------------------- bodies
class SetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    spec: dict[str, Any] = Field(description="the specification; its name defaults to the asset's")


class SetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=20)
    recommended_version_id: uuid.UUID | None = None


class SetDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    based_on_version_id: uuid.UUID | None = None
    spec: dict[str, Any] | None = None


class SetDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: dict[str, Any]


class SetGenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previews: SetPreviewSettings = Field(default_factory=SetPreviewSettings)


class SetReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    notes: str = Field(default="", max_length=4000)


class SetPublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bump: Literal["minor", "major"] = Field(
        default="minor", description="major is forced when what scenes rely on changes (size, marks, tops, ...)")


# ---------------------------------------------------------------- outputs
class _VersionBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    version_label: str | None
    based_on_version_id: uuid.UUID | None
    spec_digest: str
    built_digest: str | None
    size: list[float]
    review_decision: str | None
    published_at: datetime | None
    deprecated_at: datetime | None
    created_at: datetime | None = None
    created_by: uuid.UUID | None = None
    check_ok: bool | None = None


class _VersionDetailExtra(BaseModel):
    spec: dict[str, Any]
    style_profile_ref: str | None
    catalog_ref: str | None
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


class PropVersionSummary(_VersionBase):
    prop_id: uuid.UUID
    item_ref: str | None
    solid: bool
    rests_on: str
    top_z: float | None
    seat_z: float | None


class PropVersionDetail(_VersionDetailExtra, PropVersionSummary):
    pass


class EnvironmentVersionSummary(_VersionBase):
    environment_id: uuid.UUID
    template_ref: str | None
    lighting: str | None


class EnvironmentVersionDetail(_VersionDetailExtra, EnvironmentVersionSummary):
    layout: dict[str, Any] | None


class _OwnerBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    description: str
    tags: list[str]
    lifecycle: str
    recommended_version_id: uuid.UUID | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    recommended_version: str | None = None
    latest_published: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    thumbnail_url: str | None = None


class PropOut(_OwnerBase):
    category: str


class PropDetail(PropOut):
    versions: list[PropVersionSummary] = []


class EnvironmentOut(_OwnerBase):
    template_key: str
    kind: str


class EnvironmentDetail(EnvironmentOut):
    versions: list[EnvironmentVersionSummary] = []


class PropVersionJobOut(BaseModel):
    version: PropVersionDetail
    job: JobOut


class EnvironmentVersionJobOut(BaseModel):
    version: EnvironmentVersionDetail
    job: JobOut


class PropValidationOut(BaseModel):
    valid: bool
    issues: list[Issue]
    resolved: ResolvedProp | None = None
    digest: str | None = None


class EnvironmentValidationOut(BaseModel):
    valid: bool
    issues: list[Issue]
    resolved: ResolvedEnvironment | None = None
    layout: dict[str, Any] | None = None
    digest: str | None = None


# ---------------------------------------------------------------- the factory
def make_router(svc: SetService, *, prefix: str, Out, Detail, VSummary, VDetail, JobPair, Validation,
                filter_field: str) -> APIRouter:
    k = svc.kind
    router = APIRouter(prefix=prefix, tags=["environment library"])

    def summary(v, cls=VSummary):
        out = cls.model_validate(v, from_attributes=True)
        out.created_at = v.entry.created_at
        out.created_by = v.entry.created_by
        out.check_ok = (v.check_report or {}).get("ok") if v.check_report is not None else None
        return out

    def detail_of(v):
        out = summary(v, VDetail)
        out.files = [FileOut(**AssetFileOut.model_validate(f).model_dump(), name=f.storage_key.rsplit("/", 1)[-1])
                     for f in sorted(v.entry.files, key=lambda f: f.storage_key)]
        out.ready_for_review = svc.readiness(v) if v.status == "draft" else []
        return out

    def thumbnail(o) -> str | None:
        by_id = {v.id: v for v in o.versions}
        order = ([by_id[o.recommended_version_id]] if o.recommended_version_id in by_id else []) + sorted(
            o.versions, key=lambda v: (v.status == "published", v.id), reverse=True)
        for v in order:
            thumb = next((f for f in v.entry.files if f.role == "thumbnail"), None)
            if thumb is not None:
                return Storage.for_bucket(svc.bucket_for(v)).presign_get(thumb.storage_key)
        return None

    def owner_out(o, with_versions: bool = False):
        cls = Detail if with_versions else Out
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
        out.thumbnail_url = thumbnail(o)
        if with_versions:
            out.versions = [summary(v) for v in sorted(o.versions, key=lambda v: v.id, reverse=True)]
        return out

    def etag(v) -> str:
        return f'"{v.spec_digest[:16]}-{int(v.updated_at.timestamp() * 1_000_000)}"'

    def draft(session: Session, owner_id: uuid.UUID, draft_id: uuid.UUID):
        v = svc.get_version(session, owner_id, draft_id, lock=True)
        if v.status != "draft":
            raise ApiError(409, "not_a_draft", f"version is {v.status}; published versions never change, "
                           "create a draft based on it instead", version_status=v.status)
        return v

    @router.post("/spec/validate", response_model=Validation, summary=f"Validate a {k.label} specification")
    def validate(body: dict[str, Any], _: Principal = Depends(require("asset.read"))):
        """Always 200: an invalid spec is a normal answer here, listing every issue."""
        try:
            spec = k.spec.model_validate(body)
        except ValidationError as exc:
            return Validation(valid=False, issues=_pydantic_issues(exc))
        issues = k.validate(spec)
        if any(i.severity == "error" for i in issues):
            return Validation(valid=False, issues=issues)
        resolved = k.resolve(spec)
        extra = {"layout": resolved.layout()} if k.name == "environment" else {}
        return Validation(valid=True, issues=issues, resolved=resolved, digest=resolved.digest(), **extra)

    @router.post("", status_code=status.HTTP_201_CREATED, response_model=Detail,
                 summary=f"Create a {k.label} and its first draft from a specification")
    def create(body: SetCreate, session: Session = Depends(get_session),
               who: Principal = Depends(require("asset.create"))):
        o, v = svc.create(session, name=body.name, description=body.description, tags=_tags(body.tags),
                          spec=body.spec, created_by=who.user_id)
        audit(session, who, f"{k.name}.created", k.name, o.id, code=o.code, draft=str(v.id))
        session.commit()
        session.refresh(o)
        return owner_out(o, with_versions=True)

    @router.get("", response_model=Page[Out], summary=f"Gallery: search and filter {k.plural}")
    def gallery(session: Session = Depends(get_session), q: str | None = Query(default=None, max_length=80),
                tag: list[str] = Query(default_factory=list),
                category: str | None = Query(default=None, description="prop category or environment template"),
                status_: str | None = Query(default=None, alias="status"),
                limit: int = Query(default=48, ge=1, le=200), cursor: uuid.UUID | None = None,
                _: Principal = Depends(require("asset.read"))):
        Own, Ver = k.owner, k.model
        query = select(Own).order_by(Own.id.desc()).limit(limit + 1)
        if q:
            like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
            query = query.where(or_(Own.name.ilike(like), Own.code.ilike(like), Own.description.ilike(like)))
        if tag:
            query = query.where(Own.tags.contains(tag))
        if category:
            query = query.where(getattr(Own, filter_field) == category)
        if status_:
            sub = select(Ver.id).join(AssetVersion, AssetVersion.id == Ver.id).where(
                getattr(Ver, k.owner_field) == Own.id, AssetVersion.status == status_)
            query = query.where(exists(sub))
        if cursor:
            query = query.where(Own.id < cursor)
        rows = list(session.scalars(query))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(items=[owner_out(o) for o in rows], next_cursor=str(rows[-1].id) if more else None)

    @router.get("/{owner_id}", response_model=Detail)
    def detail(owner_id: uuid.UUID, session: Session = Depends(get_session),
               _: Principal = Depends(require("asset.read"))):
        return owner_out(svc.get_owner(session, owner_id), with_versions=True)

    @router.patch("/{owner_id}", response_model=Detail, summary="Name, description, tags, recommended version")
    def patch_identity(owner_id: uuid.UUID, body: SetPatch, session: Session = Depends(get_session),
                       who: Principal = Depends(require("asset.edit_identity"))):
        o = svc.get_owner(session, owner_id, lock=True)
        changes = body.model_dump(exclude_unset=True)
        if "recommended_version_id" in changes:
            rid = changes["recommended_version_id"]
            if rid is not None:
                v = svc.get_version(session, o.id, rid)
                if v.status != "published":
                    raise ApiError(409, "not_published", "only a published version can be recommended",
                                   version_status=v.status)
            o.recommended_version_id = rid
        if "tags" in changes:
            o.tags = _tags(changes["tags"] or [])
        for field in ("name", "description"):
            if changes.get(field) is not None:
                setattr(o, field, changes[field])
        audit(session, who, f"{k.name}.updated", k.name, o.id,
              **{f: str(v) if isinstance(v, uuid.UUID) else v for f, v in changes.items()})
        session.commit()
        session.refresh(o)
        return owner_out(o, with_versions=True)

    @router.get("/{owner_id}/versions", response_model=list[VSummary], summary="Version history, newest first")
    def history(owner_id: uuid.UUID, session: Session = Depends(get_session),
                _: Principal = Depends(require("asset.read"))):
        o = svc.get_owner(session, owner_id)
        return [summary(v) for v in sorted(o.versions, key=lambda v: v.id, reverse=True)]

    @router.get("/{owner_id}/versions/{version_id}", response_model=VDetail)
    def version_detail(owner_id: uuid.UUID, version_id: uuid.UUID, response: Response,
                       session: Session = Depends(get_session), _: Principal = Depends(require("asset.read"))):
        v = svc.get_version(session, owner_id, version_id)
        if v.status == "draft":
            response.headers["ETag"] = etag(v)
        return detail_of(v)

    @router.get("/{owner_id}/versions/{version_id}/files/{name}", summary="A version file: 302 to a presigned URL",
                responses={302: {"description": "redirect to the object"}, 200: {"model": DownloadOut}})
    def version_file(owner_id: uuid.UUID, version_id: uuid.UUID, name: str, redirect: bool = True,
                     session: Session = Depends(get_session), _: Principal = Depends(require("asset.read"))):
        v = svc.get_version(session, owner_id, version_id)
        f = svc.files_by_name(v).get(name)
        if f is None:
            raise ApiError(404, "file_not_found", f"version has no file {name!r}")
        store = Storage.for_bucket(svc.bucket_for(v))
        url = store.presign_get(f.storage_key, filename=name)
        if redirect:
            return RedirectResponse(url, status_code=302)
        return DownloadOut(url=url, expires_in_s=store.presign_expiry_s)

    @router.post("/{owner_id}/versions/{version_id}/review", response_model=VDetail,
                 summary="Approve, or reject back to draft with notes")
    def review(owner_id: uuid.UUID, version_id: uuid.UUID, body: SetReviewBody,
               session: Session = Depends(get_session), who: Principal = Depends(require("asset.review"))):
        v = svc.get_version(session, owner_id, version_id, lock=True)
        svc.review(session, v, decision=body.decision, notes=body.notes, reviewer=who.user_id)
        audit(session, who, f"{k.name}.review.{body.decision}", f"{k.name}_version", v.id, notes=body.notes)
        session.commit()
        return detail_of(v)

    @router.post("/{owner_id}/versions/{version_id}/publish", status_code=status.HTTP_202_ACCEPTED,
                 response_model=JobPair, summary="Publish an approved version (job on the publisher queue)")
    def publish(owner_id: uuid.UUID, version_id: uuid.UUID, response: Response, body: SetPublishBody | None = None,
                session: Session = Depends(get_session),
                idempotency_key: str | None = Header(default=None, max_length=200),
                who: Principal = Depends(require("asset.publish"))):
        v = svc.get_version(session, owner_id, version_id, lock=True)
        params = {"version_id": str(v.id), "bump": (body or SetPublishBody()).bump}
        replay = idempotency_key and session.scalar(select(Job.id).where(Job.idempotency_key == idempotency_key))
        if not replay:
            svc.check_publishable(v)
        job, created = submit_and_dispatch(session, who, f"publish_{k.name}", params, idempotency_key)
        if not created:
            response.status_code = status.HTTP_200_OK
        session.refresh(v)
        return JobPair(version=detail_of(v), job=job_out(job))

    @router.post("/{owner_id}/versions/{version_id}/deprecate", response_model=VDetail,
                 summary="Hide a published version from pickers; scenes that pin it still load it")
    def deprecate(owner_id: uuid.UUID, version_id: uuid.UUID, session: Session = Depends(get_session),
                  who: Principal = Depends(require("asset.publish"))):
        svc.get_owner(session, owner_id, lock=True)
        v = svc.get_version(session, owner_id, version_id, lock=True)
        svc.deprecate(session, v)
        audit(session, who, f"{k.name}.deprecated", f"{k.name}_version", v.id, version=v.version_label)
        session.commit()
        return detail_of(v)

    @router.post("/{owner_id}/drafts", status_code=status.HTTP_201_CREATED, response_model=VDetail,
                 summary="New draft, optionally copying the spec of an existing (e.g. published) version")
    def create_draft(owner_id: uuid.UUID, body: SetDraftCreate, response: Response,
                     session: Session = Depends(get_session), who: Principal = Depends(require("asset.create"))):
        o = svc.get_owner(session, owner_id, lock=True)
        v = svc.new_draft(session, o, based_on_version_id=body.based_on_version_id, spec=body.spec,
                          created_by=who.user_id)
        audit(session, who, f"{k.name}.draft_created", f"{k.name}_version", v.id,
              based_on=str(body.based_on_version_id) if body.based_on_version_id else None)
        session.commit()
        session.refresh(v)
        response.headers["ETag"] = etag(v)
        return detail_of(v)

    @router.patch("/{owner_id}/drafts/{draft_id}", response_model=VDetail, summary="Edit a draft's spec")
    def edit_draft(owner_id: uuid.UUID, draft_id: uuid.UUID, body: SetDraftPatch, response: Response,
                   session: Session = Depends(get_session), if_match: str | None = Header(default=None),
                   who: Principal = Depends(require("asset.edit_draft"))):
        v = draft(session, owner_id, draft_id)
        if if_match is not None and if_match != etag(v):
            raise ApiError(412, "stale_draft", "the draft changed since you read it; reload and try again")
        svc.update_draft(session, v, body.spec)
        audit(session, who, f"{k.name}.draft_edited", f"{k.name}_version", v.id, spec_digest=v.spec_digest)
        session.commit()
        session.refresh(v)
        response.headers["ETag"] = etag(v)
        return detail_of(v)

    @router.post("/{owner_id}/drafts/{draft_id}/generate", status_code=status.HTTP_202_ACCEPTED,
                 response_model=JobPair, summary="Build the draft in Blender and measure it (job)")
    def generate(owner_id: uuid.UUID, draft_id: uuid.UUID, response: Response, body: SetGenerateBody | None = None,
                 session: Session = Depends(get_session),
                 idempotency_key: str | None = Header(default=None, max_length=200),
                 who: Principal = Depends(require("asset.create"))):
        v = draft(session, owner_id, draft_id)
        params = {"spec": v.spec, "previews": (body or SetGenerateBody()).previews.model_dump(),
                  "draft_version_id": str(v.id)}
        job, created = submit_and_dispatch(session, who, f"generate_{k.name}", params, idempotency_key,
                                           before_commit=lambda job: svc.begin_generate(v, job.id))
        if not created:
            response.status_code = status.HTTP_200_OK
        session.refresh(v)
        return JobPair(version=detail_of(v), job=job_out(job))

    @router.post("/{owner_id}/drafts/{draft_id}/submit", response_model=VDetail,
                 summary="Send a built draft whose measured check passed to review")
    def submit(owner_id: uuid.UUID, draft_id: uuid.UUID, session: Session = Depends(get_session),
               who: Principal = Depends(require("asset.submit"))):
        v = draft(session, owner_id, draft_id)
        svc.submit(session, v)
        audit(session, who, f"{k.name}.submitted", f"{k.name}_version", v.id)
        session.commit()
        return detail_of(v)

    @router.delete("/{owner_id}/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT,
                   summary="Discard a draft")
    def discard(owner_id: uuid.UUID, draft_id: uuid.UUID, session: Session = Depends(get_session),
                who: Principal = Depends(require("asset.edit_draft"))) -> Response:
        v = draft(session, owner_id, draft_id)
        vid = v.id
        svc.discard_draft(session, v)
        audit(session, who, f"{k.name}.draft_discarded", f"{k.name}_version", vid)
        session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # unique operation ids per kind, so the generated client has props_* and environments_* calls
    for route in router.routes:
        route.operation_id = f"{k.plural}_{route.name}"  # type: ignore[attr-defined]
    return router


props_router = make_router(props, prefix="/props", Out=PropOut, Detail=PropDetail, VSummary=PropVersionSummary,
                           VDetail=PropVersionDetail, JobPair=PropVersionJobOut, Validation=PropValidationOut,
                           filter_field="category")
environments_router = make_router(environments, prefix="/environments", Out=EnvironmentOut,
                                  Detail=EnvironmentDetail, VSummary=EnvironmentVersionSummary,
                                  VDetail=EnvironmentVersionDetail, JobPair=EnvironmentVersionJobOut,
                                  Validation=EnvironmentValidationOut, filter_field="template_key")
