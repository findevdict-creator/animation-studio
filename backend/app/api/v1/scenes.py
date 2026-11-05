"""Scene endpoints (SRS 16.2, 16.3, Phase 5 Character Assembly).

A scene version is an immutable manifest whose pins were resolved against the registry when
it was created. ``assemble`` builds its ``scene.blend`` (a Blender job on the blender queue;
the API never runs Blender); ``reopen`` opens that saved file in a fresh workspace rebuilt
from the pins and checks it against the manifest, and checks the pinned source files are
byte-identical. Scenes are never published and nothing here changes a library asset.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session
from studio_contracts.jobs import ScenePreviewSettings
from studio_contracts.scene import CLIP_CATALOG, ClipDef

from app.api.v1.jobs import _out as job_out
from app.api.v1.jobs import submit_and_dispatch
from app.api.v1.library import _tags
from app.api.v1.schemas import DownloadOut, JobOut, Page
from app.assets import scenes as svc
from app.core.auth import Principal, audit, require
from app.core.database import get_session
from app.core.errors import ApiError
from app.core.storage import Storage
from app.models.scenes import Scene, SceneInstance, SceneInstanceAccessory, SceneVersion

router = APIRouter(prefix="/scenes", tags=["scenes"])
catalog_router = APIRouter(tags=["scenes"])


# ---------------------------------------------------------------- bodies
class SceneCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    manifest: dict[str, Any] = Field(description="SceneManifest; its name defaults to the scene's")
    notes: str = Field(default="", max_length=2000)
    allow_verified_fits: bool = Field(
        default=False, description="director override: accept outfit fits that are verified but not yet approved")


class ScenePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=20)


class SceneVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: dict[str, Any]
    based_on_version_id: uuid.UUID | None = Field(
        default=None, description="the version this one changes; deprecated pins it already had stay allowed")
    notes: str = Field(default="", max_length=2000)
    allow_verified_fits: bool = False


class SceneValidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: dict[str, Any]
    scene_id: uuid.UUID | None = None
    based_on_version_id: uuid.UUID | None = None
    allow_verified_fits: bool = False


class SceneJobBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previews: ScenePreviewSettings = Field(default_factory=ScenePreviewSettings)


# ---------------------------------------------------------------- responses
class SceneIssue(BaseModel):
    path: str
    code: str
    message: str
    severity: str = "error"


class SceneValidationOut(BaseModel):
    ok: bool
    issues: list[SceneIssue]
    resolved: dict[str, Any] | None = Field(default=None, description="ResolvedScene when ok")


class SceneFile(BaseModel):
    name: str
    role: str
    sha256: str
    size_bytes: int
    mime_type: str
    url: str | None = None


class SceneAccessoryPin(BaseModel):
    accessory_version_id: uuid.UUID
    attach_point: str


class SceneInstanceOut(BaseModel):
    key: str
    character_version_id: uuid.UUID
    outfit_version_id: uuid.UUID | None
    outfit_compatibility_id: uuid.UUID | None
    compatibility_status: str | None
    clips: list[str]
    accessories: list[SceneAccessoryPin]


class SceneVersionSummary(BaseModel):
    id: uuid.UUID
    number: int
    based_on_version_id: uuid.UUID | None
    manifest_digest: str
    notes: str
    created_by: uuid.UUID | None
    created_at: datetime
    build_status: str
    verify_status: str
    instances: int
    overrides: int
    thumbnail_url: str | None = None


class SceneVersionDetail(SceneVersionSummary):
    manifest: dict[str, Any]
    resolved: dict[str, Any]
    overrides_detail: list[dict[str, Any]]
    pinned: list[SceneInstanceOut]
    assemble_job_id: uuid.UUID | None
    scene_blend_sha256: str | None
    build_report: dict[str, Any] | None
    build_files: list[SceneFile]
    blender_version: str | None
    built_at: datetime | None
    verify_job_id: uuid.UUID | None
    verify_report: dict[str, Any] | None
    verify_files: list[SceneFile]
    verified_at: datetime | None


class SceneOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str
    tags: list[str]
    lifecycle: str
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    latest_version: SceneVersionSummary | None = None


class SceneDetail(SceneOut):
    versions: list[SceneVersionSummary] = Field(default_factory=list)


class SceneVersionJobOut(BaseModel):
    version: SceneVersionDetail
    job: JobOut


class SceneUsage(BaseModel):
    scene_id: uuid.UUID
    scene_code: str
    scene_name: str
    scene_version_id: uuid.UUID
    number: int
    instance_key: str
    build_status: str


# ---------------------------------------------------------------- helpers
def _store() -> Storage:
    return Storage.for_bucket("work")


def _files(raw: list[dict[str, Any]], with_urls: bool) -> list[SceneFile]:
    store = _store() if with_urls and raw else None
    return [SceneFile(name=f["name"], role=f["role"], sha256=f["sha256"], size_bytes=f["size_bytes"],
                      mime_type=f["mime_type"],
                      url=store.presign_get(f["storage_key"], filename=f["name"]) if store else None) for f in raw]


def _thumb(v: SceneVersion) -> str | None:
    f = next((f for f in v.build_files or [] if f["role"] == "thumbnail"), None)
    return _store().presign_get(f["storage_key"]) if f else None


def _summary(session: Session, v: SceneVersion, cls=SceneVersionSummary, **extra: Any) -> Any:
    return cls(id=v.id, number=v.number, based_on_version_id=v.based_on_version_id, manifest_digest=v.manifest_digest,
               notes=v.notes, created_by=v.created_by, created_at=v.created_at,
               build_status=svc.build_status(session, v), verify_status=svc.verify_status(session, v),
               instances=len(v.instances), overrides=len(v.overrides or []), thumbnail_url=_thumb(v), **extra)


def _detail(session: Session, v: SceneVersion) -> SceneVersionDetail:
    pinned = [SceneInstanceOut(key=i.key, character_version_id=i.character_version_id,
                               outfit_version_id=i.outfit_version_id,
                               outfit_compatibility_id=i.outfit_compatibility_id,
                               compatibility_status=i.compatibility_status, clips=list(i.clips or []),
                               accessories=[SceneAccessoryPin(accessory_version_id=a.accessory_version_id,
                                                              attach_point=a.attach_point) for a in i.accessories])
              for i in v.instances]
    return _summary(session, v, SceneVersionDetail, manifest=v.manifest, resolved=v.resolved,
                    overrides_detail=list(v.overrides or []), pinned=pinned, assemble_job_id=v.assemble_job_id,
                    scene_blend_sha256=v.scene_blend_sha256, build_report=v.build_report,
                    build_files=_files(v.build_files or [], True), blender_version=v.blender_version,
                    built_at=v.built_at, verify_job_id=v.verify_job_id, verify_report=v.verify_report,
                    verify_files=_files(v.verify_files or [], True), verified_at=v.verified_at)


def _scene(session: Session, s: Scene, with_versions: bool = False) -> Any:
    base = {k: getattr(s, k) for k in ("id", "code", "name", "description", "tags", "lifecycle", "created_by",
                                       "created_at", "updated_at")}
    latest = _summary(session, s.versions[-1]) if s.versions else None
    if with_versions:
        return SceneDetail(**base, latest_version=latest, versions=[_summary(session, v) for v in s.versions])
    return SceneOut(**base, latest_version=latest)


def _override_allowed(who: Principal, wanted: bool) -> None:
    if wanted and not who.can("asset.publish"):
        raise ApiError(403, "forbidden", "accepting a verified but unapproved fit is a director's override",
                       required="asset.publish")


def _audit_overrides(session: Session, who: Principal, v: SceneVersion) -> None:
    for o in v.overrides or []:
        audit(session, who, "scene.fit_override", "scene_version", v.id, **o)


# ---------------------------------------------------------------- catalog
@catalog_router.get("/animation-clips", response_model=list[ClipDef],
                    summary="The built-in animation clip catalog (procedural stand-ins until Phase 6)")
def list_clips(_: Principal = Depends(require("asset.read"))) -> list[ClipDef]:
    return list(CLIP_CATALOG.values())


# ---------------------------------------------------------------- scenes
@router.post("/validate", response_model=SceneValidationOut,
             summary="Dry run: check a manifest against the registry and show its resolution")
def validate(body: SceneValidate, session: Session = Depends(get_session),
             _: Principal = Depends(require("asset.read"))) -> SceneValidationOut:
    m = svc.parse_manifest(body.manifest)
    base = None
    if body.based_on_version_id:
        if body.scene_id is None:
            raise ApiError(422, "scene_id_required", "based_on_version_id needs scene_id")
        base = svc.get_version(session, body.scene_id, body.based_on_version_id)
    res = svc.resolve(session, m, base=base, allow_verified_fits=body.allow_verified_fits)
    return SceneValidationOut(ok=res.ok, issues=[SceneIssue(**i.model_dump()) for i in res.issues],
                              resolved=res.resolved.model_dump(mode="json") if res.ok else None)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SceneDetail,
             summary="Create a scene and its first version from a manifest")
def create(body: SceneCreate, session: Session = Depends(get_session),
           who: Principal = Depends(require("scene.edit"))) -> SceneDetail:
    _override_allowed(who, body.allow_verified_fits)
    s, v = svc.create_scene(session, name=body.name, description=body.description, tags=_tags(body.tags),
                            manifest=body.manifest, notes=body.notes, allow_verified_fits=body.allow_verified_fits,
                            created_by=who.user_id)
    audit(session, who, "scene.created", "scene", s.id, code=s.code, version=str(v.id),
          manifest_digest=v.manifest_digest)
    _audit_overrides(session, who, v)
    session.commit()
    session.refresh(s)
    return _scene(session, s, with_versions=True)


@router.get("", response_model=Page[SceneOut], summary="List scenes, newest first")
def list_scenes(session: Session = Depends(get_session), q: str | None = Query(default=None, max_length=80),
                tag: list[str] = Query(default_factory=list),
                uses_version_id: uuid.UUID | None = Query(
                    default=None, description="scenes with a version pinning this character, outfit or accessory "
                                              "version"),
                limit: int = Query(default=48, ge=1, le=200), cursor: uuid.UUID | None = None,
                _: Principal = Depends(require("asset.read"))) -> Page[SceneOut]:
    query = select(Scene).order_by(Scene.id.desc()).limit(limit + 1)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        query = query.where(or_(Scene.name.ilike(like), Scene.code.ilike(like), Scene.description.ilike(like)))
    if tag:
        query = query.where(Scene.tags.contains(tag))
    if uses_version_id:
        acc = select(SceneInstanceAccessory.instance_id).where(
            SceneInstanceAccessory.accessory_version_id == uses_version_id)
        sub = select(SceneInstance.id).join(SceneVersion, SceneVersion.id == SceneInstance.scene_version_id).where(
            SceneVersion.scene_id == Scene.id,
            or_(SceneInstance.character_version_id == uses_version_id,
                SceneInstance.outfit_version_id == uses_version_id, SceneInstance.id.in_(acc)))
        query = query.where(exists(sub))
    if cursor:
        query = query.where(Scene.id < cursor)
    rows = list(session.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    return Page(items=[_scene(session, s) for s in rows], next_cursor=str(rows[-1].id) if more else None)


@router.get("/usage/{version_id}", response_model=list[SceneUsage],
            summary="Every scene version and instance that pins this asset version")
def usage(version_id: uuid.UUID, session: Session = Depends(get_session),
          _: Principal = Depends(require("asset.read"))) -> list[SceneUsage]:
    acc = select(SceneInstanceAccessory.instance_id).where(SceneInstanceAccessory.accessory_version_id == version_id)
    rows = session.execute(
        select(SceneInstance, SceneVersion, Scene).join(SceneVersion, SceneVersion.id == SceneInstance.scene_version_id)
        .join(Scene, Scene.id == SceneVersion.scene_id)
        .where(or_(SceneInstance.character_version_id == version_id, SceneInstance.outfit_version_id == version_id,
                   SceneInstance.id.in_(acc)))
        .order_by(Scene.code, SceneVersion.number, SceneInstance.position)).all()
    return [SceneUsage(scene_id=s.id, scene_code=s.code, scene_name=s.name, scene_version_id=v.id, number=v.number,
                       instance_key=i.key, build_status=svc.build_status(session, v)) for i, v, s in rows]


@router.get("/{scene_id}", response_model=SceneDetail)
def detail(scene_id: uuid.UUID, session: Session = Depends(get_session),
           _: Principal = Depends(require("asset.read"))) -> SceneDetail:
    return _scene(session, svc.get_scene(session, scene_id), with_versions=True)


@router.patch("/{scene_id}", response_model=SceneDetail, summary="Name, description, tags")
def patch(scene_id: uuid.UUID, body: ScenePatch, session: Session = Depends(get_session),
          who: Principal = Depends(require("scene.edit"))) -> SceneDetail:
    s = svc.get_scene(session, scene_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    if "tags" in changes:
        changes["tags"] = _tags(changes["tags"] or [])
    for k, val in changes.items():
        if val is not None:
            setattr(s, k, val)
    audit(session, who, "scene.updated", "scene", s.id, fields=sorted(changes))
    session.commit()
    session.refresh(s)
    return _scene(session, s, with_versions=True)


@router.post("/{scene_id}/versions", status_code=status.HTTP_201_CREATED, response_model=SceneVersionDetail,
             summary="A new version from a manifest (versions never change)")
def create_version(scene_id: uuid.UUID, body: SceneVersionCreate, session: Session = Depends(get_session),
                   who: Principal = Depends(require("scene.edit"))) -> SceneVersionDetail:
    _override_allowed(who, body.allow_verified_fits)
    s = svc.get_scene(session, scene_id, lock=True)
    base = svc.get_version(session, scene_id, body.based_on_version_id) if body.based_on_version_id else None
    v = svc.new_version(session, s, manifest=body.manifest, based_on=base, notes=body.notes,
                        allow_verified_fits=body.allow_verified_fits, created_by=who.user_id)
    audit(session, who, "scene.version_created", "scene_version", v.id, scene=s.code, number=v.number,
          manifest_digest=v.manifest_digest)
    _audit_overrides(session, who, v)
    session.commit()
    return _detail(session, svc.get_version(session, scene_id, v.id))


@router.get("/{scene_id}/versions/{version_id}", response_model=SceneVersionDetail)
def version_detail(scene_id: uuid.UUID, version_id: uuid.UUID, session: Session = Depends(get_session),
                   _: Principal = Depends(require("asset.read"))) -> SceneVersionDetail:
    return _detail(session, svc.get_version(session, scene_id, version_id))


@router.post("/{scene_id}/versions/{version_id}/assemble", status_code=status.HTTP_202_ACCEPTED,
             response_model=SceneVersionJobOut, summary="Build scene.blend for this version (Blender job)")
def assemble(scene_id: uuid.UUID, version_id: uuid.UUID, body: SceneJobBody, response: Response,
             session: Session = Depends(get_session), idempotency_key: str | None = Header(default=None, max_length=200),
             who: Principal = Depends(require("scene.edit"))) -> SceneVersionJobOut:
    v = svc.get_version(session, scene_id, version_id, lock=True)
    svc.check_pins_still_usable(session, v)
    if svc.build_status(session, v) not in ("none", "failed"):
        svc.begin_assemble(session, v, v.assemble_job_id)  # raises with the reason
    params = {"scene_version_id": str(v.id), "resolved": v.resolved, "previews": body.previews.model_dump()}
    job, created = submit_and_dispatch(session, who, "assemble_scene", params, idempotency_key,
                                       before_commit=lambda job: svc.begin_assemble(session, v, job.id))
    if not created:
        response.status_code = status.HTTP_200_OK
    return SceneVersionJobOut(version=_detail(session, svc.get_version(session, scene_id, version_id)),
                              job=job_out(job))


@router.post("/{scene_id}/versions/{version_id}/reopen", status_code=status.HTTP_202_ACCEPTED,
             response_model=SceneVersionJobOut,
             summary="Reopen the saved scene.blend in a fresh workspace and check it against the manifest (job)")
def reopen(scene_id: uuid.UUID, version_id: uuid.UUID, body: SceneJobBody, response: Response,
           session: Session = Depends(get_session), idempotency_key: str | None = Header(default=None, max_length=200),
           who: Principal = Depends(require("scene.edit"))) -> SceneVersionJobOut:
    v = svc.get_version(session, scene_id, version_id, lock=True)
    if svc.build_status(session, v) != "built" or svc.verify_status(session, v) == "queued":
        svc.begin_verify(session, v, v.verify_job_id)  # raises with the reason
    params = {"scene_version_id": str(v.id), "resolved": v.resolved, "scene_blend_sha256": v.scene_blend_sha256,
              "previews": body.previews.model_dump()}
    job, created = submit_and_dispatch(session, who, "verify_scene", params, idempotency_key,
                                       before_commit=lambda job: svc.begin_verify(session, v, job.id))
    if not created:
        response.status_code = status.HTTP_200_OK
    return SceneVersionJobOut(version=_detail(session, svc.get_version(session, scene_id, version_id)),
                              job=job_out(job))


@router.get("/{scene_id}/versions/{version_id}/files/{name}", summary="A build or reopen file: 302 to a presigned URL",
            responses={302: {"description": "redirect to the object"}, 200: {"model": DownloadOut}})
def version_file(scene_id: uuid.UUID, version_id: uuid.UUID, name: str, redirect: bool = True,
                 session: Session = Depends(get_session), _: Principal = Depends(require("asset.read"))):
    v = svc.get_version(session, scene_id, version_id)
    f = next((f for f in [*(v.build_files or []), *(v.verify_files or [])] if f["name"] == name), None)
    if f is None:
        raise ApiError(404, "file_not_found", f"version has no file {name!r}")
    store = _store()
    url = store.presign_get(f["storage_key"], filename=name)
    if redirect:
        return RedirectResponse(url, status_code=302)
    return DownloadOut(url=url, expires_in_s=store.presign_expiry_s)
