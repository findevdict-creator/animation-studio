"""Prop and environment library service: identity, drafts, review and publish (SRS 12).

Both kinds follow the lifecycle every library asset has (draft -> generate -> submit ->
review -> publish, the database triggers of migration 0011 as the last line), so one
``SetService`` serves both; what differs is in ``SetKind``. ``props`` and ``environments``
are the two instances, and they have the module-level names the publisher expects of a
service (``get_version``, ``readiness``, ``check_publishable``, ``next_number``,
``library_prefix``, ``complete_publish`` and ``get_prop`` / ``get_environment``).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from studio_contracts.environment import (
    EnvironmentSpecification,
    PropSpecification,
    resolve_environment,
    resolve_prop,
    validate_environment,
    validate_prop,
)
from studio_contracts.ids import AssetKind, uuid7

from app.assets import registry
from app.core.errors import ApiError
from app.models.assets import AssetFile
from app.models.sets import Environment, EnvironmentVersion, Prop, PropVersion

REQUIRED_ROLES = ("blend", "spec", "report", "thumbnail")
USABLE_STATUSES = ("published", "deprecated")


def now() -> datetime:
    return datetime.now(UTC)


def _prop_meta(version: PropVersion, r) -> None:
    version.item_ref = r.item
    version.solid = r.solid
    version.rests_on = r.rests_on
    version.top_z = r.surface_z("top")
    version.seat_z = r.surface_z("seat")


def _env_meta(version: EnvironmentVersion, r) -> None:
    version.template_ref = r.template
    version.lighting = r.lighting.key
    version.layout = r.layout()


def _prop_identity(r) -> dict[str, Any]:
    return {"category": r.category}


def _env_identity(r) -> dict[str, Any]:
    return {"template_key": r.key, "kind": r.kind}


@dataclass(frozen=True)
class SetKind:
    name: str  # prop | environment
    plural: str
    asset_kind: AssetKind
    owner: type
    model: type
    owner_field: str
    spec: type[BaseModel]
    validate: Callable
    resolve: Callable
    identity: Callable  # resolved -> the owner columns that never change
    meta: Callable  # (version, resolved) -> the version's metadata columns
    blend: str
    sha_key: str
    label: str


PROP = SetKind("prop", "props", AssetKind.prop, Prop, PropVersion, "prop_id", PropSpecification, validate_prop,
               resolve_prop, _prop_identity, _prop_meta, "prop.blend", "prop_blend_sha256", "prop")
ENVIRONMENT = SetKind("environment", "environments", AssetKind.environment, Environment, EnvironmentVersion,
                      "environment_id", EnvironmentSpecification, validate_environment, resolve_environment,
                      _env_identity, _env_meta, "environment.blend", "environment_blend_sha256", "environment")


class SetService:
    def __init__(self, kind: SetKind):
        self.kind = kind
        setattr(self, f"get_{kind.name}", self.get_owner)

    # ------------------------------------------------------------ spec
    def checked_spec(self, raw: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        """Validate a specification; 422 lists every issue at once."""
        k = self.kind
        try:
            spec = k.spec.model_validate(raw)
        except ValidationError as exc:
            issues = [{"path": ".".join(str(p) for p in e["loc"]) or "$", "code": e["type"], "message": e["msg"]}
                      for e in exc.errors(include_url=False, include_context=False)]
            raise ApiError(422, "invalid_spec", f"the {k.label} specification is not valid", issues=issues) from None
        errors = [i for i in k.validate(spec) if i.severity == "error"]
        if errors:
            raise ApiError(422, "invalid_spec", f"the {k.label} specification is not valid",
                           issues=[i.model_dump() for i in errors])
        return spec.model_dump(mode="json"), k.resolve(spec)

    def _apply_spec(self, version, spec: dict[str, Any], r) -> None:
        version.spec = spec
        version.spec_digest = r.digest()
        version.style_profile_ref = r.style_profile
        version.catalog_ref = r.catalog
        version.size = [float(v) for v in r.size]
        self.kind.meta(version, r)

    def _same_identity(self, owner, r) -> None:
        for field, value in self.kind.identity(r).items():
            if getattr(owner, field) != value:
                raise ApiError(422, f"{field}_changed", f"a {self.kind.label}'s {field.replace('_', ' ')} never "
                               f"changes; create a new {self.kind.label}", **{field: getattr(owner, field)})

    # ------------------------------------------------------------ lookups
    def get_owner(self, session: Session, owner_id: uuid.UUID, *, lock: bool = False):
        k = self.kind
        q = select(k.owner).where(k.owner.id == owner_id)
        if lock:
            q = q.with_for_update(of=k.owner)
        o = session.scalar(q)
        if o is None:
            raise ApiError(404, f"{k.name}_not_found", str(owner_id))
        return o

    def get_version(self, session: Session, owner_id: uuid.UUID, version_id: uuid.UUID, *, lock: bool = False):
        k = self.kind
        q = select(k.model).where(k.model.id == version_id, getattr(k.model, k.owner_field) == owner_id)
        if lock:
            q = q.with_for_update().execution_options(populate_existing=True)
        v = session.scalar(q)
        if v is None:
            raise ApiError(404, f"{k.name}_version_not_found", str(version_id))
        return v

    def by_code(self, session: Session, code: str):
        return session.scalar(select(self.kind.owner).where(self.kind.owner.code == code))

    @staticmethod
    def _need(version, *statuses: str) -> None:
        if version.status not in statuses:
            raise ApiError(409, "invalid_version_status",
                           f"the version is {version.status}; this needs {' or '.join(statuses)}",
                           version_status=version.status)

    # ------------------------------------------------------------ identity and drafts
    def create(self, session: Session, *, name: str, description: str, tags: list[str], spec: dict[str, Any],
               created_by: uuid.UUID | None):
        k = self.kind
        clean, r = self.checked_spec({**spec, "name": spec.get("name") or name})
        owner = k.owner(id=uuid7(), code=registry.allocate_code(session, k.asset_kind), name=name,
                        description=description, tags=sorted(set(tags)), created_by=created_by, **k.identity(r))
        session.add(owner)
        session.flush()
        return owner, self._new_version(session, owner, clean, r, created_by=created_by)

    def _new_version(self, session: Session, owner, spec: dict[str, Any], r, *, created_by: uuid.UUID | None,
                     based_on: uuid.UUID | None = None):
        k = self.kind
        reg = registry.create_draft(session, k.asset_kind, asset_id=owner.id, code=owner.code, created_by=created_by)
        version = k.model(id=reg.id, based_on_version_id=based_on, review_notes="", **{k.owner_field: owner.id})
        self._apply_spec(version, spec, r)
        session.add(version)
        session.flush()
        session.refresh(version)
        return version

    def new_draft(self, session: Session, owner, *, based_on_version_id: uuid.UUID | None,
                  spec: dict[str, Any] | None, created_by: uuid.UUID | None):
        if based_on_version_id is not None:
            source = self.get_version(session, owner.id, based_on_version_id)
            raw = spec if spec is not None else source.spec
        elif spec is not None:
            raw = spec
        else:
            raise ApiError(422, "spec_required", "give based_on_version_id or a spec")
        clean, r = self.checked_spec({"name": owner.name, **raw})
        self._same_identity(owner, r)
        return self._new_version(session, owner, clean, r, created_by=created_by, based_on=based_on_version_id)

    def update_draft(self, session: Session, version, spec: dict[str, Any]):
        self._need(version, "draft")
        clean, r = self.checked_spec({"name": version.owner.name, **spec})
        self._same_identity(version.owner, r)
        self._apply_spec(version, clean, r)
        if version.review_decision == "reject":
            version.review_decision = None
        session.flush()
        return version

    def discard_draft(self, session: Session, version) -> None:
        k = self.kind
        self._need(version, "draft")
        if session.scalar(select(k.model.id).where(k.model.based_on_version_id == version.id)) is not None:
            raise ApiError(409, "draft_referenced", "another version is based on this draft")
        for f in list(version.entry.files):
            session.delete(f)
        session.flush()
        reg = version.entry
        session.delete(version)
        session.flush()
        session.delete(reg)
        session.flush()

    def begin_generate(self, version, job_id: uuid.UUID) -> None:
        self._need(version, "draft")
        version.generate_job_id = job_id

    def attach_build(self, session: Session, version, job_id: uuid.UUID, uploaded: list[dict[str, Any]],
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

    # ------------------------------------------------------------ review
    def readiness(self, version) -> list[str]:
        k = self.kind
        problems = []
        if version.built_digest is None:
            problems.append("no build yet: run generate")
        elif version.built_digest != version.spec_digest:
            problems.append("the spec changed since the last build: run generate again")
        report = version.check_report or {}
        if version.built_digest is not None and not report.get("ok"):
            failed = ", ".join(report.get("failed", [])[:6])
            problems.append(f"the measured check failed ({failed})" if failed else "the measured check did not pass")
        files = {f.role: f for f in version.entry.files}
        missing = [r for r in REQUIRED_ROLES if r not in files]
        if missing:
            problems.append(f"missing files: {', '.join(missing)}")
        elif report.get(k.sha_key) != files["blend"].sha256:
            problems.append(f"the check report does not describe this {k.blend}")
        return problems

    def submit(self, session: Session, version):
        self._need(version, "draft")
        problems = self.readiness(version)
        if problems:
            raise ApiError(409, "not_ready_for_review", "; ".join(problems), problems=problems)
        version.review_decision = None
        version.reviewed_by = None
        version.reviewed_at = None
        session.flush()
        version.entry.status = "review"
        session.flush()
        return version

    def review(self, session: Session, version, *, decision: str, notes: str, reviewer: uuid.UUID | None):
        self._need(version, "review")
        version.review_decision = decision
        version.review_notes = notes
        version.reviewed_by = reviewer
        version.reviewed_at = now()
        session.flush()
        if decision == "reject":
            version.entry.status = "draft"
            session.flush()
        return version

    def check_publishable(self, version) -> None:
        self._need(version, "review")
        if version.review_decision != "approve":
            raise ApiError(409, "not_approved", "a reviewer must approve the version before it is published")

    def deprecate(self, session: Session, version):
        self._need(version, "published")
        version.entry.status = "deprecated"
        session.flush()
        version.deprecated_at = now()
        session.flush()
        return version

    # ------------------------------------------------------------ files
    @staticmethod
    def files_by_name(version) -> dict[str, AssetFile]:
        return {f.storage_key.rsplit("/", 1)[-1]: f for f in version.entry.files}

    @staticmethod
    def bucket_for(version) -> str:
        return "library" if version.status in USABLE_STATUSES else "work"

    # ------------------------------------------------------------ publishing
    def breaking(self, old, new) -> list[str]:
        """What changed in a way that breaks scenes pinning the previous version: major."""
        reasons = []
        if old.style_profile_ref != new.style_profile_ref:
            reasons.append("style profile")
        if [round(x, 3) for x in old.size] != [round(x, 3) for x in new.size]:
            reasons.append("size")
        if self.kind.name == "prop":
            if old.solid != new.solid or old.rests_on != new.rests_on:
                reasons.append("placement")
            if old.top_z is not None and new.top_z != old.top_z:
                reasons.append("top height")
        else:
            gone = {m["name"] for m in (old.layout or {}).get("marks", [])} - \
                   {m["name"] for m in (new.layout or {}).get("marks", [])}
            gone_cams = {c["name"] for c in (old.layout or {}).get("cameras", [])} - \
                        {c["name"] for c in (new.layout or {}).get("cameras", [])}
            if gone:
                reasons.append("marks")
            if gone_cams:
                reasons.append("camera marks")
            if [t[0] for t in (old.layout or {}).get("tops", [])] != [t[0] for t in (new.layout or {}).get("tops", [])]:
                reasons.append("tops")
        return reasons

    def next_number(self, session: Session, version, bump: str) -> tuple[int, int, list[str]]:
        published = [v for v in version.owner.versions if v.status in USABLE_STATUSES and v.id != version.id]
        if not published:
            return 1, 0, []
        latest = max(published, key=lambda v: (v.entry.major, v.entry.minor))
        reasons = self.breaking(latest, version)
        if reasons or bump == "major":
            return latest.entry.major + 1, 0, reasons
        return latest.entry.major, latest.entry.minor + 1, []

    def library_prefix(self, version, major: int, minor: int) -> str:
        return f"library/{self.kind.plural}/{version.owner_id}/versions/{major}.{minor}/"

    def complete_publish(self, session: Session, version, *, major: int, minor: int, files: list[dict[str, Any]],
                         manifest: dict[str, Any], published_by: uuid.UUID | None, expected_build: str,
                         job_id: uuid.UUID):
        self.check_publishable(version)
        if version.built_digest != expected_build:
            raise ApiError(409, "build_changed", "the version's build changed while it was being published")
        for f in list(version.entry.files):
            session.delete(f)
        session.flush()
        for f in files:
            registry.add_file(session, version.entry, role=f["role"], storage_key=f["storage_key"],
                              sha256=f["sha256"], size_bytes=f["size_bytes"], mime_type=f["mime_type"],
                              blender_version=f.get("blender_version"), job_id=job_id)
        version.manifest_key = manifest["storage_key"]
        version.manifest_sha256 = manifest["sha256"]
        version.published_by = published_by
        version.published_at = now()
        session.flush()
        version.entry.major, version.entry.minor, version.entry.status = major, minor, "published"
        session.flush()
        owner = version.owner
        if owner.recommended_version_id is None:
            owner.recommended_version_id = version.id
            session.flush()
        session.expire(version.entry, ["files"])
        return version


props = SetService(PROP)
environments = SetService(ENVIRONMENT)
SERVICES = {"prop": props, "environment": environments}
