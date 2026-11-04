"""Character Library endpoints (architecture 12.1): identity, drafts, builds, review, history.

Published versions are read-only everywhere; the only way to change one is a new draft
based on it. Every response that names a file can hand out a short-lived presigned URL; the
browser never sees storage credentials.
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
from app.api.v1.schemas import AssetFileOut, DownloadOut, JobOut, Page
from app.assets import characters as svc
from app.core.auth import Principal, audit, require
from app.core.database import get_session
from app.core.errors import ApiError
from app.core.storage import Storage
from app.models.assets import AssetVersion
from app.models.characters import Character, CharacterVersion
from app.models.jobs import Job
from studio_contracts.jobs import PreviewSettings

router = APIRouter(prefix="/characters", tags=["character library"])


# ---------------------------------------------------------------- bodies
class CharacterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    spec: dict[str, Any] = Field(description="CharacterSpecification; its name defaults to the character's")


class CharacterPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=20)
    recommended_version_id: uuid.UUID | None = None


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    based_on_version_id: uuid.UUID | None = None
    spec: dict[str, Any] | None = None


class DraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: dict[str, Any]


class GenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previews: PreviewSettings = Field(default_factory=PreviewSettings)


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    notes: str = Field(default="", max_length=4000)


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bump: Literal["minor", "major"] = Field(
        default="minor", description="major is forced when the template family, rig or fit profile changed")


class FileOut(AssetFileOut):
    name: str = ""


class VersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    character_id: uuid.UUID
    status: str
    version_label: str | None
    based_on_version_id: uuid.UUID | None
    spec_digest: str
    built_digest: str | None
    fit_profile: str | None
    height_m: float | None
    review_decision: str | None
    published_at: datetime | None
    deprecated_at: datetime | None
    created_at: datetime | None = None
    created_by: uuid.UUID | None = None


class VersionDetail(VersionSummary):
    spec: dict[str, Any]
    template_ref: str | None
    style_profile_ref: str | None
    rig_profile_ref: str | None
    blender_version: str | None
    validation_report: dict[str, Any] | None
    generate_job_id: uuid.UUID | None
    review_notes: str
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    manifest_key: str | None
    manifest_sha256: str | None
    published_by: uuid.UUID | None
    files: list[FileOut] = []
    ready_for_review: list[str] = Field(default_factory=list, description="what blocks submit; empty when ready")


class CharacterOut(BaseModel):
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


class CharacterDetail(CharacterOut):
    versions: list[VersionSummary] = []


class VersionJobOut(BaseModel):
    version: VersionDetail
    job: JobOut


# ---------------------------------------------------------------- helpers
def _summary(v: CharacterVersion, cls=VersionSummary) -> Any:
    out = cls.model_validate(v, from_attributes=True)
    out.created_at = v.entry.created_at
    out.created_by = v.entry.created_by
    return out


def _detail(v: CharacterVersion) -> VersionDetail:
    out = _summary(v, VersionDetail)
    out.files = [FileOut(**AssetFileOut.model_validate(f).model_dump(), name=f.storage_key.rsplit("/", 1)[-1])
                 for f in sorted(v.entry.files, key=lambda f: f.storage_key)]
    out.ready_for_review = svc.readiness(v) if v.status == "draft" else []
    return out


def _thumbnail(c: Character) -> str | None:
    by_id = {v.id: v for v in c.versions}
    order = ([by_id[c.recommended_version_id]] if c.recommended_version_id in by_id else []) + sorted(
        c.versions, key=lambda v: (v.status == "published", v.id), reverse=True)
    for v in order:
        thumb = next((f for f in v.entry.files if f.role == "thumbnail"), None)
        if thumb is not None:
            return Storage.for_bucket(svc.bucket_for(v)).presign_get(thumb.storage_key)
    return None


def _character(c: Character, with_versions: bool = False) -> CharacterOut:
    cls = CharacterDetail if with_versions else CharacterOut
    out = cls.model_validate(c, from_attributes=True)
    counts: dict[str, int] = {}
    for v in c.versions:
        counts[v.status] = counts.get(v.status, 0) + 1
    out.counts = counts
    published = [v for v in c.versions if v.status == "published"]
    if published:
        out.latest_published = max(published, key=lambda v: (v.entry.major, v.entry.minor)).version_label
    rec = next((v for v in c.versions if v.id == c.recommended_version_id), None)
    out.recommended_version = rec.version_label if rec else None
    out.thumbnail_url = _thumbnail(c)
    if with_versions:
        out.versions = [_summary(v) for v in sorted(c.versions, key=lambda v: v.id, reverse=True)]
    return out


def _etag(v: CharacterVersion) -> str:
    return f'"{v.spec_digest[:16]}-{int(v.updated_at.timestamp() * 1_000_000)}"'


def _draft(session: Session, character_id: uuid.UUID, draft_id: uuid.UUID, *, lock: bool = True) -> CharacterVersion:
    v = svc.get_version(session, character_id, draft_id, lock=lock)
    if v.status != "draft":
        raise ApiError(409, "not_a_draft", f"version is {v.status}; published versions never change, "
                       "create a draft based on it instead", version_status=v.status)
    return v


def _tags(tags: list[str]) -> list[str]:
    import re

    bad = [t for t in tags if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", t)]
    if bad:
        raise ApiError(422, "invalid_tag", "tags are lower-case letters, digits, - and _", tags=bad)
    return sorted(set(tags))


# ---------------------------------------------------------------- identity
@router.post("", status_code=status.HTTP_201_CREATED, response_model=CharacterDetail,
             summary="Create a character and its first draft from a specification")
def create(body: CharacterCreate, session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.create"))) -> CharacterDetail:
    c, v = svc.create_character(session, name=body.name, description=body.description, tags=_tags(body.tags),
                                spec=body.spec, created_by=who.user_id)
    audit(session, who, "character.created", "character", c.id, code=c.code, draft=str(v.id))
    session.commit()
    session.refresh(c)
    return _character(c, with_versions=True)


@router.get("", response_model=Page[CharacterOut], summary="Gallery: search and filter characters")
def gallery(session: Session = Depends(get_session), q: str | None = Query(default=None, max_length=80),
            tag: list[str] = Query(default_factory=list), status_: str | None = Query(default=None, alias="status"),
            fit_profile: str | None = None, limit: int = Query(default=48, ge=1, le=200),
            cursor: uuid.UUID | None = None, _: Principal = Depends(require("asset.read"))) -> Page[CharacterOut]:
    query = select(Character).order_by(Character.id.desc()).limit(limit + 1)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        query = query.where(or_(Character.name.ilike(like), Character.code.ilike(like),
                                Character.description.ilike(like)))
    if tag:
        query = query.where(Character.tags.contains(tag))
    if status_ or fit_profile:
        sub = select(CharacterVersion.id).join(AssetVersion, AssetVersion.id == CharacterVersion.id).where(
            CharacterVersion.character_id == Character.id)
        if status_:
            sub = sub.where(AssetVersion.status == status_)
        if fit_profile:
            sub = sub.where(CharacterVersion.fit_profile == fit_profile)
        query = query.where(exists(sub))
    if cursor:
        query = query.where(Character.id < cursor)
    rows = list(session.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    return Page(items=[_character(c) for c in rows], next_cursor=str(rows[-1].id) if more else None)


@router.get("/{character_id}", response_model=CharacterDetail)
def detail(character_id: uuid.UUID, session: Session = Depends(get_session),
           _: Principal = Depends(require("asset.read"))) -> CharacterDetail:
    return _character(svc.get_character(session, character_id), with_versions=True)


@router.patch("/{character_id}", response_model=CharacterDetail, summary="Name, description, tags, recommended version")
def patch_identity(character_id: uuid.UUID, body: CharacterPatch, session: Session = Depends(get_session),
                   who: Principal = Depends(require("asset.edit_identity"))) -> CharacterDetail:
    c = svc.get_character(session, character_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    if "recommended_version_id" in changes:
        rid = changes["recommended_version_id"]
        if rid is not None:
            v = svc.get_version(session, c.id, rid)
            if v.status != "published":
                raise ApiError(409, "not_published", "only a published version can be recommended", version_status=v.status)
        c.recommended_version_id = rid
    if "tags" in changes:
        c.tags = _tags(changes["tags"] or [])
    for field in ("name", "description"):
        if changes.get(field) is not None:
            setattr(c, field, changes[field])
    audit(session, who, "character.updated", "character", c.id,
          **{k: str(v) if isinstance(v, uuid.UUID) else v for k, v in changes.items()})
    session.commit()
    session.refresh(c)
    return _character(c, with_versions=True)


# ---------------------------------------------------------------- versions
@router.get("/{character_id}/versions", response_model=list[VersionSummary], summary="Version history, newest first")
def history(character_id: uuid.UUID, session: Session = Depends(get_session),
            _: Principal = Depends(require("asset.read"))) -> list[VersionSummary]:
    c = svc.get_character(session, character_id)
    return [_summary(v) for v in sorted(c.versions, key=lambda v: v.id, reverse=True)]


@router.get("/{character_id}/versions/{version_id}", response_model=VersionDetail)
def version_detail(character_id: uuid.UUID, version_id: uuid.UUID, response: Response,
                   session: Session = Depends(get_session),
                   _: Principal = Depends(require("asset.read"))) -> VersionDetail:
    v = svc.get_version(session, character_id, version_id)
    if v.status == "draft":
        response.headers["ETag"] = _etag(v)
    return _detail(v)


@router.get("/{character_id}/versions/{version_id}/files/{name}", summary="A version file: 302 to a presigned URL",
            responses={302: {"description": "redirect to the object"}, 200: {"model": DownloadOut}})
def version_file(character_id: uuid.UUID, version_id: uuid.UUID, name: str, redirect: bool = True,
                 session: Session = Depends(get_session), _: Principal = Depends(require("asset.read"))):
    v = svc.get_version(session, character_id, version_id)
    f = svc.files_by_name(v).get(name)
    if f is None:
        raise ApiError(404, "file_not_found", f"version has no file {name!r}")
    store = Storage.for_bucket(svc.bucket_for(v))
    url = store.presign_get(f.storage_key, filename=name)
    if redirect:
        return RedirectResponse(url, status_code=302)
    return DownloadOut(url=url, expires_in_s=store.presign_expiry_s)


@router.post("/{character_id}/versions/{version_id}/review", response_model=VersionDetail,
             summary="Approve, or reject back to draft with notes")
def review(character_id: uuid.UUID, version_id: uuid.UUID, body: ReviewBody, session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.review"))) -> VersionDetail:
    v = svc.get_version(session, character_id, version_id, lock=True)
    svc.review(session, v, decision=body.decision, notes=body.notes, reviewer=who.user_id)
    audit(session, who, f"character.review.{body.decision}", "character_version", v.id, notes=body.notes)
    session.commit()
    return _detail(v)


@router.post("/{character_id}/versions/{version_id}/publish", status_code=status.HTTP_202_ACCEPTED,
             response_model=VersionJobOut, summary="Publish an approved version (job on the publisher queue)")
def publish(character_id: uuid.UUID, version_id: uuid.UUID, response: Response, body: PublishBody | None = None,
            session: Session = Depends(get_session), idempotency_key: str | None = Header(default=None, max_length=200),
            who: Principal = Depends(require("asset.publish"))) -> VersionJobOut:
    v = svc.get_version(session, character_id, version_id, lock=True)
    params = {"version_id": str(v.id), "bump": (body or PublishBody()).bump}
    replay = idempotency_key and session.scalar(select(Job.id).where(Job.idempotency_key == idempotency_key))
    if not replay:  # a replayed request returns its job, even once the version is published
        svc.check_publishable(v)
    job, created = submit_and_dispatch(session, who, "publish_character", params, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    session.refresh(v)
    return VersionJobOut(version=_detail(v), job=job_out(job))


@router.post("/{character_id}/versions/{version_id}/deprecate", response_model=VersionDetail,
             summary="Hide a published version from pickers; it stays loadable")
def deprecate(character_id: uuid.UUID, version_id: uuid.UUID, session: Session = Depends(get_session),
              who: Principal = Depends(require("asset.publish"))) -> VersionDetail:
    svc.get_character(session, character_id, lock=True)
    v = svc.get_version(session, character_id, version_id, lock=True)
    svc.deprecate(session, v)
    audit(session, who, "character.deprecated", "character_version", v.id, version=v.version_label)
    session.commit()
    return _detail(v)


# ---------------------------------------------------------------- drafts
@router.post("/{character_id}/drafts", status_code=status.HTTP_201_CREATED, response_model=VersionDetail,
             summary="New draft, optionally copying the spec of an existing (e.g. published) version")
def create_draft(character_id: uuid.UUID, body: DraftCreate, response: Response,
                 session: Session = Depends(get_session),
                 who: Principal = Depends(require("asset.create"))) -> VersionDetail:
    c = svc.get_character(session, character_id, lock=True)
    v = svc.new_draft(session, c, based_on_version_id=body.based_on_version_id, spec=body.spec,
                      created_by=who.user_id)
    audit(session, who, "character.draft_created", "character_version", v.id,
          based_on=str(body.based_on_version_id) if body.based_on_version_id else None)
    session.commit()
    session.refresh(v)
    response.headers["ETag"] = _etag(v)
    return _detail(v)


@router.patch("/{character_id}/drafts/{draft_id}", response_model=VersionDetail, summary="Edit a draft's spec")
def edit_draft(character_id: uuid.UUID, draft_id: uuid.UUID, body: DraftPatch, response: Response,
               session: Session = Depends(get_session), if_match: str | None = Header(default=None),
               who: Principal = Depends(require("asset.edit_draft"))) -> VersionDetail:
    v = _draft(session, character_id, draft_id)
    if if_match is not None and if_match != _etag(v):
        raise ApiError(412, "stale_draft", "the draft changed since you read it; reload and try again")
    svc.update_draft(session, v, body.spec)
    audit(session, who, "character.draft_edited", "character_version", v.id, spec_digest=v.spec_digest)
    session.commit()
    session.refresh(v)
    response.headers["ETag"] = _etag(v)
    return _detail(v)


@router.post("/{character_id}/drafts/{draft_id}/generate", status_code=status.HTTP_202_ACCEPTED,
             response_model=VersionJobOut, summary="Build the draft in Blender (job)")
def generate(character_id: uuid.UUID, draft_id: uuid.UUID, response: Response, body: GenerateBody | None = None,
             session: Session = Depends(get_session), idempotency_key: str | None = Header(default=None, max_length=200),
             who: Principal = Depends(require("asset.create"))) -> VersionJobOut:
    v = _draft(session, character_id, draft_id)
    params = {"spec": v.spec, "previews": (body or GenerateBody()).previews.model_dump(),
              "draft_version_id": str(v.id)}
    job, created = submit_and_dispatch(session, who, "generate_character", params, idempotency_key,
                                       before_commit=lambda job: svc.begin_generate(v, job.id))
    if not created:
        response.status_code = status.HTTP_200_OK
    session.refresh(v)
    return VersionJobOut(version=_detail(v), job=job_out(job))


@router.post("/{character_id}/drafts/{draft_id}/submit", response_model=VersionDetail,
             summary="Send a built, validated draft to review")
def submit(character_id: uuid.UUID, draft_id: uuid.UUID, session: Session = Depends(get_session),
           who: Principal = Depends(require("asset.submit"))) -> VersionDetail:
    v = _draft(session, character_id, draft_id)
    svc.submit(session, v)
    audit(session, who, "character.submitted", "character_version", v.id)
    session.commit()
    return _detail(v)


@router.delete("/{character_id}/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Discard a draft")
def discard(character_id: uuid.UUID, draft_id: uuid.UUID, session: Session = Depends(get_session),
            who: Principal = Depends(require("asset.edit_draft"))) -> Response:
    v = _draft(session, character_id, draft_id)
    vid = v.id
    svc.discard_draft(session, v)
    audit(session, who, "character.draft_discarded", "character_version", vid)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
