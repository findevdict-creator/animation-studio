"""The publisher (queue ``publish``, architecture 6.3): an approved asset version in review
becomes an immutable library version. Characters, outfits and accessories go through the same steps;
what differs per kind (which file Blender re-checks, the manifest, the service module) is
in ``KINDS``.

    claim job -> read the version (must be in review and approved, with a current build)
    -> download every draft file, checking each SHA-256 against its row
    -> fresh Blender process re-checks the exact .blend being published (scripts disabled)
    -> claim the version number with a write-once marker in the library bucket
    -> write-once copy of every file, the publish-time report and manifest.json
    -> one transaction: library file rows, manifest, publisher, status published, audit

This worker runs with the publisher storage identity, the only one that can write the
library bucket. Every library key is written once (``If-None-Match: *``); a retry after a
crash finds its own objects and continues. The number is claimed in storage, not in the
database, so a failed publish never leaves a published row and never reuses a number that
already has objects: a number whose upload was abandoned is skipped.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import studio_contracts
from app.assets import accessories as accessory_svc
from app.assets import characters as svc
from app.assets import outfits as outfit_svc
from app.config import Settings
from app.core.errors import ApiError
from app.core.storage import ChecksumMismatch, ObjectExists, Storage, sha256_file
from app.jobs import service
from app.models.accessories import AccessoryVersion
from app.models.audit import AuditLog
from app.models.characters import CharacterVersion
from app.models.outfits import OutfitVersion
from studio_contracts.jobs import (
    JOB_QUEUES,
    InputFile,
    JobEnvelope,
    JobStatus,
    JobType,
    PublishAccessoryParams,
    PublishCharacterParams,
    PublishOutfitParams,
)
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1

from studio_workers.supervisor import RunOutcome, Supervisor, _read_result

log = logging.getLogger("studio.worker.publisher")
CLAIM = "version.claim.json"
MAX_SKIPS = 20


class PublishRefused(Exception):
    """Permanent: retrying cannot help (not approved, bad checksum, failed validation, …)."""


class _Stopped(Exception):
    def __init__(self, status: JobStatus, error: dict[str, Any]):
        self.status, self.error = status, error


def _canonical(doc: dict[str, Any]) -> bytes:
    return (json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n").encode()


@dataclass(frozen=True)
class Kind:
    name: str  # character | outfit | accessory
    job_type: JobType
    params: type
    model: type
    blend: str  # the file Blender re-checks
    sha_key: str  # where the re-check report names the SHA-256 of that file
    stamp: str  # the digest stamp in the file that must equal the version's spec digest

    @property
    def svc(self) -> ModuleType:  # looked up on every call, so tests can patch the module
        return {"character": svc, "outfit": outfit_svc, "accessory": accessory_svc}[self.name]

    @property
    def owner_field(self) -> str:
        return f"{self.name}_id"

    def get_owner(self, session, owner_id):
        return getattr(self.svc, f"get_{self.name}")(session, owner_id, lock=True)


KINDS: dict[str, Kind] = {
    JobType.publish_character.value: Kind("character", JobType.publish_character, PublishCharacterParams,
                                          CharacterVersion, "character.blend", "blend_sha256", "studio_spec_digest"),
    JobType.publish_outfit.value: Kind("outfit", JobType.publish_outfit, PublishOutfitParams, OutfitVersion,
                                       "outfit.blend", "outfit_blend_sha256", "studio_outfit_digest"),
    JobType.publish_accessory.value: Kind("accessory", JobType.publish_accessory, PublishAccessoryParams,
                                          AccessoryVersion, "accessory.blend", "accessory_blend_sha256",
                                          "studio_accessory_digest"),
}


class PublisherRunner(Supervisor):
    def __init__(self, settings: Settings | None = None, work_storage: Storage | None = None,
                 library_storage: Storage | None = None, **kw: Any):
        super().__init__(settings, work_storage=work_storage, **kw)
        self._library = library_storage

    @property
    def library(self) -> Storage:
        if self._library is None:
            self._library = Storage.for_bucket("library", self.settings)
        return self._library

    # ------------------------------------------------------------------ run
    def run(self, job_id: uuid.UUID) -> RunOutcome:
        with self.sessions.begin() as s:
            job = service.claim(s, job_id, self.host)
            if job is None:
                return RunOutcome(None)
            kind = KINDS[job.type]
            params = kind.params.model_validate(job.params)
            attempt, requested_by = job.attempt, job.requested_by
            timeout = self.settings.job_timeouts_s.get(job.type, self.settings.default_job_timeout_s)
        workspace = Path(self.settings.work_root) / str(job_id) / f"attempt-{attempt}"
        shutil.rmtree(workspace, ignore_errors=True)
        for d in ("inputs", "out", "tmp", "library"):
            (workspace / d).mkdir(parents=True)
        log_key = None
        status = JobStatus.failed
        try:
            result = self._publish(kind, job_id, attempt, params, requested_by, workspace, timeout)
            status = JobStatus.succeeded
            return RunOutcome(status)
        except PublishRefused as exc:
            log_key = self._upload_log(job_id, attempt, workspace)
            return self._end(job_id, JobStatus.failed, {"message": str(exc), "kind": "refused",
                                                        "log_tail": self._log_tail(workspace)}, log_key)
        except _Stopped as exc:
            log_key = self._upload_log(job_id, attempt, workspace)
            status = exc.status
            return self._end(job_id, exc.status, exc.error, log_key)
        except Exception as exc:  # storage, network, a crashed Blender: resume on the next attempt
            log.exception("publish job %s attempt %d", job_id, attempt)
            log_key = self._upload_log(job_id, attempt, workspace)
            error = {"message": f"{type(exc).__name__}: {exc}"[:2000], "kind": "transient",
                     "log_tail": self._log_tail(workspace)}
            with self.sessions.begin() as s:
                retried = service.requeue_for_retry(s, job_id, error)
                if not retried:
                    service.finish(s, job_id, JobStatus.failed, error=error, log_key=log_key)
            if retried:
                self.dispatcher.send(job_id, JOB_QUEUES[kind.job_type].value,
                                     countdown_s=5 * 2 ** (attempt - 1))
                return RunOutcome(JobStatus.queued, retried=True)
            return RunOutcome(JobStatus.failed)
        finally:
            if status == JobStatus.succeeded or not self.settings.keep_failed_workspaces:
                shutil.rmtree(workspace, ignore_errors=True)

    def _upload_log(self, job_id: uuid.UUID, attempt: int, workspace: Path) -> str | None:
        """The publisher identity cannot write the work bucket (or anything but the library),
        so the Blender log is not uploaded; a failure carries its tail instead."""
        return None

    @staticmethod
    def _log_tail(workspace: Path, size: int = 4000) -> str | None:
        path = workspace / "blender.log"
        if not path.exists():
            return None
        return path.read_bytes()[-size:].decode("utf-8", "replace")

    def _end(self, job_id: uuid.UUID, status: JobStatus, error: dict[str, Any], log_key: str | None) -> RunOutcome:
        with self.sessions.begin() as s:
            service.finish(s, job_id, status, error=error, log_key=log_key)
        return RunOutcome(status)

    def _progress(self, job_id: uuid.UUID, value: float, message: str) -> None:
        with self.sessions.begin() as s:
            service.report_progress(s, job_id, value, message)

    # -------------------------------------------------------------- publish
    def _publish(self, kind: Kind, job_id: uuid.UUID, attempt: int, params: Any,
                 requested_by: uuid.UUID | None, workspace: Path, timeout: float) -> dict[str, Any]:
        snap = self._snapshot(kind, params.version_id)
        if snap.get("already"):
            with self.sessions.begin() as s:
                service.finish(s, job_id, JobStatus.succeeded, result=snap["already"])
            return snap["already"]

        self._progress(job_id, 0.05, "downloading the draft files")
        local: dict[str, Path] = {}
        for f in snap["files"]:
            dest = workspace / "inputs" / f["name"]
            try:
                self.storage.get_file(f["storage_key"], dest, expected_sha256=f["sha256"])
            except ChecksumMismatch as exc:
                raise PublishRefused(f"{f['name']} in the draft no longer matches its recorded SHA-256: {exc}") from exc
            local[f["name"]] = dest

        self._progress(job_id, 0.15, f"re-checking {kind.blend} in a fresh Blender process")
        report = self._revalidate(kind, job_id, attempt, workspace, snap, timeout)

        major, minor, prefix = self._claim_number(kind, params, snap, workspace)
        self._progress(job_id, 0.7, f"copying to the library as {major}.{minor}")
        files = []
        for f in snap["files"]:
            files.append(self._put(prefix + f["name"], local[f["name"]], f["mime_type"], f["role"],
                                   snap["blender_version"]))
        report_path = workspace / "out" / "publish_validation.json"
        files.append(self._put(prefix + "publish_validation.json", report_path, "application/json", "report",
                               report["blender_version"], adopt=True))

        manifest_doc = {"character": self._manifest, "outfit": self._outfit_manifest,
                        "accessory": self._accessory_manifest}[kind.name](snap, major, minor, files, report)
        manifest_path = workspace / "library" / "manifest.json"
        manifest_path.write_bytes(_canonical(manifest_doc))
        manifest = self._put(prefix + "manifest.json", manifest_path, "application/json", "manifest", None,
                             adopt=True, adopt_check=lambda doc: doc.get("version_id") == snap["version_id"])

        self._progress(job_id, 0.95, "recording the published version")
        result = {kind.owner_field: snap[kind.owner_field], "asset_version_id": snap["version_id"],
                  "display_code": snap["code"], "version": f"{major}.{minor}", "manifest_key": manifest["storage_key"],
                  "manifest_sha256": manifest["sha256"], "library_prefix": prefix,
                  "files": [{"name": f["storage_key"].rsplit("/", 1)[-1], "sha256": f["sha256"]} for f in files]}
        with self.sessions.begin() as s:
            ksvc = kind.svc
            owner_id = uuid.UUID(snap[kind.owner_field])
            owner = kind.get_owner(s, owner_id)
            version = ksvc.get_version(s, owner.id, uuid.UUID(snap["version_id"]), lock=True)
            try:
                ksvc.complete_publish(s, version, major=major, minor=minor, files=[*files, manifest],
                                      manifest=manifest, published_by=requested_by,
                                      expected_build=snap["built_digest"], job_id=job_id)
            except ApiError as exc:
                raise PublishRefused(f"{exc.code}: {exc.detail}") from exc
            s.add(AuditLog(actor_id=requested_by, action=f"{kind.name}.published", target_kind=f"{kind.name}_version",
                           target_id=version.id, detail={"version": f"{major}.{minor}", "code": snap["code"],
                                                         "manifest_sha256": manifest["sha256"], "job_id": str(job_id)}))
            service.finish(s, job_id, JobStatus.succeeded, result=result)
        return result

    def _snapshot(self, kind: Kind, version_id: uuid.UUID) -> dict[str, Any]:
        if kind.name == "outfit":
            return self._outfit_snapshot(version_id)
        if kind.name == "accessory":
            return self._accessory_snapshot(version_id)
        with self.sessions() as s:
            v = s.get(CharacterVersion, version_id)
            if v is None:
                raise PublishRefused(f"character version {version_id} does not exist")
            if v.status in ("published", "deprecated"):  # a replay after success
                return {"already": {"asset_version_id": str(v.id), "character_id": str(v.character_id),
                                    "version": v.version_label, "manifest_key": v.manifest_key,
                                    "manifest_sha256": v.manifest_sha256, "already_published": True}}
            try:
                svc.check_publishable(v)
            except ApiError as exc:
                raise PublishRefused(exc.detail) from exc
            problems = svc.readiness(v)
            if problems:
                raise PublishRefused("; ".join(problems))
            based_on = s.get(CharacterVersion, v.based_on_version_id) if v.based_on_version_id else None
            return {
                "version_id": str(v.id), "character_id": str(v.character_id), "code": v.entry.display_code,
                "name": v.spec.get("name"), "spec_digest": v.spec_digest, "built_digest": v.built_digest,
                "template_ref": v.template_ref, "style_profile_ref": v.style_profile_ref,
                "rig_profile_ref": v.rig_profile_ref, "fit_profile": v.fit_profile, "height_m": v.height_m,
                "blender_version": v.blender_version, "draft_report": v.validation_report,
                "reviewed_by": str(v.reviewed_by) if v.reviewed_by else None,
                "reviewed_at": v.reviewed_at.isoformat() if v.reviewed_at else None, "review_notes": v.review_notes,
                "based_on": ({"version_id": str(based_on.id), "version": based_on.version_label}
                             if based_on else None),
                "files": [{"name": f.storage_key.rsplit("/", 1)[-1], "role": f.role, "storage_key": f.storage_key,
                           "sha256": f.sha256, "size_bytes": f.size_bytes, "mime_type": f.mime_type}
                          for f in sorted(v.entry.files, key=lambda f: f.storage_key)],
            }

    def _revalidate(self, kind: Kind, job_id: uuid.UUID, attempt: int, workspace: Path, snap: dict[str, Any],
                    timeout: float) -> dict[str, Any]:
        blend = next((f for f in snap["files"] if f["name"] == kind.blend), None)
        if blend is None:
            raise PublishRefused(f"the version has no {kind.blend}")
        params = {"version_id": snap["version_id"]}
        if kind.name in ("outfit", "accessory"):
            params["spec"] = snap["spec"]
        envelope = JobEnvelope(job_id=job_id, type=kind.job_type, attempt=attempt, params=params,
                               inputs=[InputFile(path=f"inputs/{kind.blend}", sha256=blend["sha256"])],
                               contracts_version=studio_contracts.__version__)
        job_json = workspace / "job.json"
        job_json.write_text(envelope.model_dump_json(indent=2))
        status, error, exit_code = self._execute(job_id, workspace, job_json, timeout)
        if status is not None:
            raise _Stopped(status, error or {})
        result = _read_result(workspace / "result.json")
        if result is None:
            raise RuntimeError(f"the validation process exited with code {exit_code} and no result")
        if result.status == "failed":
            raise PublishRefused(f"re-validation failed: {result.error}")
        report, stamps = result.data["report"], result.data["stamps"]
        if not report["ok"]:
            raise PublishRefused(f"{kind.blend} failed re-validation: {report['failed']}")
        if report.get(kind.sha_key) != blend["sha256"]:
            raise PublishRefused(f"the validated file is not the draft's {kind.blend}")
        if stamps.get(kind.stamp) != snap["built_digest"] or snap["built_digest"] != snap["spec_digest"]:
            raise PublishRefused(f"{kind.blend} was not built from the version's specification")
        return report

    def _claim_number(self, kind: Kind, params: Any, snap: dict[str, Any],
                      workspace: Path) -> tuple[int, int, str]:
        ksvc = kind.svc
        with self.sessions() as s:
            v = s.get(kind.model, uuid.UUID(snap["version_id"]))
            major, minor, reasons = ksvc.next_number(s, v, params.bump)
            if reasons and params.bump == "minor":
                log.info("publish %s: major bump forced (%s changed)", snap["version_id"], ", ".join(reasons))
            prefix_of = lambda M, m: ksvc.library_prefix(v, M, m)
        claim = workspace / "library" / CLAIM
        claim.write_bytes(_canonical({"version_id": snap["version_id"], kind.owner_field: snap[kind.owner_field]}))
        sha = sha256_file(claim)
        for _ in range(MAX_SKIPS):
            prefix = prefix_of(major, minor)
            try:
                self.library.put_immutable(prefix + CLAIM, claim, "application/json", sha)
                return major, minor, prefix
            except ObjectExists:  # another version's abandoned or concurrent publish holds it
                log.warning("publish %s: %d.%d is claimed by another version; skipping it",
                            snap["version_id"], major, minor)
                major, minor = (major + 1, 0) if minor == 0 and (params.bump == "major" or reasons) else (major, minor + 1)
        raise RuntimeError(f"no free version number after {MAX_SKIPS} tries")

    def _put(self, key: str, path: Path, mime: str, role: str, blender_version: str | None, *,
             adopt: bool = False, adopt_check=None) -> dict[str, Any]:
        """Write once. A file this publish derived itself (report, manifest) may already exist
        from an interrupted attempt with slightly different bytes; inside our claimed prefix it
        is adopted (after a sanity check) rather than refused."""
        sha = sha256_file(path)
        try:
            info = self.library.put_immutable(key, path, mime, sha)
            return {"role": role, "storage_key": key, "sha256": info.sha256 or sha, "size_bytes": path.stat().st_size,
                    "mime_type": mime, "blender_version": blender_version}
        except ObjectExists:
            if not adopt:
                raise PublishRefused(f"{key} already exists in the library with different content") from None
            body = self.library.get_bytes(key)
            doc = json.loads(body)
            if adopt_check is not None and not adopt_check(doc):
                raise PublishRefused(f"{key} belongs to another version") from None
            return {"role": role, "storage_key": key, "sha256": hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body), "mime_type": mime, "blender_version": blender_version}

    @staticmethod
    def _manifest(snap: dict[str, Any], major: int, minor: int, files: list[dict[str, Any]],
                  report: dict[str, Any]) -> dict[str, Any]:
        """AssetVersionManifest (architecture 4.3): a function of the frozen version only, so a
        resumed publish writes the same bytes."""
        draft = snap["draft_report"] or {}
        return {
            "manifest_version": "asset_manifest/1",
            "kind": "character", "code": snap["code"], "character_id": snap["character_id"],
            "version": f"{major}.{minor}", "version_id": snap["version_id"], "name": snap["name"],
            "based_on": snap["based_on"],
            "files": [{"role": f["role"], "key": f["storage_key"].rsplit("/", 1)[-1], "sha256": f["sha256"],
                       "size": f["size_bytes"], "mime": f["mime_type"]}
                      for f in sorted(files, key=lambda f: f["storage_key"])],
            "dependencies": [{"kind": "template", "ref": snap["template_ref"]},
                             {"kind": "rig_profile", "ref": snap["rig_profile_ref"]},
                             {"kind": "style_profile", "ref": snap["style_profile_ref"]}],
            "rig_profile": snap["rig_profile_ref"], "body_fit_profile": snap["fit_profile"],
            "height_m": snap["height_m"], "spec_digest": snap["spec_digest"],
            "shape_keys": {"expressions": CARTOON_HUMANOID_V1.expression_keys(),
                           "visemes": CARTOON_HUMANOID_V1.viseme_keys(), "eyes": list(CARTOON_HUMANOID_V1.eye_keys)},
            "blender_version": snap["blender_version"], "engine_version": draft.get("engine_version"),
            "validation": {"build": {"ok": draft.get("ok"), "checks": len(draft.get("checks", []))},
                           "publish": {"ok": report["ok"], "checks": len(report["checks"]),
                                       "blender_version": report["blender_version"]}},
            "approved_by": snap["reviewed_by"], "approved_at": snap["reviewed_at"],
        }

    # -------------------------------------------------------------- outfits
    def _outfit_snapshot(self, version_id: uuid.UUID) -> dict[str, Any]:
        from app.models.characters import Character
        from app.models.outfits import Outfit, OutfitCompatibility

        with self.sessions() as s:
            v = s.get(OutfitVersion, version_id)
            if v is None:
                raise PublishRefused(f"outfit version {version_id} does not exist")
            if v.status in ("published", "deprecated"):
                return {"already": {"asset_version_id": str(v.id), "outfit_id": str(v.outfit_id),
                                    "version": v.version_label, "manifest_key": v.manifest_key,
                                    "manifest_sha256": v.manifest_sha256, "already_published": True}}
            try:
                outfit_svc.check_publishable(v)
            except ApiError as exc:
                raise PublishRefused(exc.detail) from exc
            problems = outfit_svc.readiness(s, v)
            if problems:
                raise PublishRefused("; ".join(problems))
            outfit: Outfit = v.outfit
            owner = s.get(Character, outfit.owner_character_id) if outfit.owner_character_id else None
            blend = outfit_svc.blend_file(v)
            fits = []
            for row in s.query(OutfitCompatibility).filter(OutfitCompatibility.outfit_version_id == v.id,
                                                           OutfitCompatibility.status.in_(outfit_svc.GOOD_FIT),
                                                           OutfitCompatibility.outfit_blend_sha256 == blend.sha256):
                cv = s.get(CharacterVersion, row.character_version_id)
                fits.append({"character_id": str(cv.character_id), "character_version_id": str(cv.id),
                             "code": cv.entry.display_code, "version": cv.version_label, "status": row.status,
                             "fit_profile": row.fit_profile, "character_blend_sha256": row.character_blend_sha256})
            based_on = s.get(OutfitVersion, v.based_on_version_id) if v.based_on_version_id else None
            return {
                "version_id": str(v.id), "outfit_id": str(v.outfit_id), "code": v.entry.display_code,
                "name": v.spec.get("name"), "spec": v.spec, "spec_digest": v.spec_digest,
                "built_digest": v.built_digest, "scope": outfit.scope,
                "owner": ({"character_id": str(owner.id), "code": owner.code} if owner else None),
                "style_profile_ref": v.style_profile_ref, "rig_profile_ref": v.rig_profile_ref,
                "catalog_ref": v.catalog_ref, "fit_profiles": list(v.fit_profiles),
                "primary_fit_profile": v.primary_fit_profile, "slots": list(v.slots),
                "blender_version": v.blender_version, "draft_report": v.fit_report,
                "fits": sorted(fits, key=lambda f: f["character_version_id"]),
                "reviewed_by": str(v.reviewed_by) if v.reviewed_by else None,
                "reviewed_at": v.reviewed_at.isoformat() if v.reviewed_at else None, "review_notes": v.review_notes,
                "based_on": ({"version_id": str(based_on.id), "version": based_on.version_label}
                             if based_on else None),
                "files": [{"name": f.storage_key.rsplit("/", 1)[-1], "role": f.role, "storage_key": f.storage_key,
                           "sha256": f.sha256, "size_bytes": f.size_bytes, "mime_type": f.mime_type}
                          for f in sorted(v.entry.files, key=lambda f: f.storage_key)],
            }

    @staticmethod
    def _outfit_manifest(snap: dict[str, Any], major: int, minor: int, files: list[dict[str, Any]],
                         report: dict[str, Any]) -> dict[str, Any]:
        """AssetVersionManifest for an outfit. It names who the outfit is for and the fits
        verified when it was published, but never carries a character: wearers are linked
        at their own published versions."""
        draft = snap["draft_report"] or {}
        pieces = [{"slot": p["slot"], "garment": f"{p['garment']['id']}@{p['garment']['version']}"}
                  for p in snap["spec"].get("pieces", [])]
        return {
            "manifest_version": "asset_manifest/1",
            "kind": "outfit", "code": snap["code"], "outfit_id": snap["outfit_id"],
            "version": f"{major}.{minor}", "version_id": snap["version_id"], "name": snap["name"],
            "based_on": snap["based_on"], "scope": snap["scope"], "owner_character": snap["owner"],
            "files": [{"role": f["role"], "key": f["storage_key"].rsplit("/", 1)[-1], "sha256": f["sha256"],
                       "size": f["size_bytes"], "mime": f["mime_type"]}
                      for f in sorted(files, key=lambda f: f["storage_key"])],
            "dependencies": [{"kind": "rig_profile", "ref": snap["rig_profile_ref"]},
                             {"kind": "style_profile", "ref": snap["style_profile_ref"]},
                             {"kind": "garment_catalog", "ref": snap["catalog_ref"]}],
            "rig_profile": snap["rig_profile_ref"], "fit_profiles": snap["fit_profiles"],
            "primary_fit_profile": snap["primary_fit_profile"], "slots": snap["slots"],
            "pieces": pieces,
            "spec_digest": snap["spec_digest"], "verified_fits": snap["fits"],
            "blender_version": snap["blender_version"], "engine_version": draft.get("engine_version"),
            "validation": {"build": {"ok": draft.get("ok"), "checks": len(draft.get("checks", []))},
                           "publish": {"ok": report["ok"], "checks": len(report.get("checks", [])),
                                       "blender_version": report.get("blender_version")}},
            "approved_by": snap["reviewed_by"], "approved_at": snap["reviewed_at"],
        }

    # -------------------------------------------------------------- accessories
    def _accessory_snapshot(self, version_id: uuid.UUID) -> dict[str, Any]:
        with self.sessions() as s:
            v = s.get(AccessoryVersion, version_id)
            if v is None:
                raise PublishRefused(f"accessory version {version_id} does not exist")
            if v.status in ("published", "deprecated"):
                return {"already": {"asset_version_id": str(v.id), "accessory_id": str(v.accessory_id),
                                    "version": v.version_label, "manifest_key": v.manifest_key,
                                    "manifest_sha256": v.manifest_sha256, "already_published": True}}
            try:
                accessory_svc.check_publishable(v)
            except ApiError as exc:
                raise PublishRefused(exc.detail) from exc
            problems = accessory_svc.readiness(v)
            if problems:
                raise PublishRefused("; ".join(problems))
            based_on = s.get(AccessoryVersion, v.based_on_version_id) if v.based_on_version_id else None
            return {
                "version_id": str(v.id), "accessory_id": str(v.accessory_id), "code": v.entry.display_code,
                "name": v.spec.get("name"), "spec": v.spec, "spec_digest": v.spec_digest,
                "built_digest": v.built_digest, "category": v.accessory.category, "item_ref": v.item_ref,
                "style_profile_ref": v.style_profile_ref, "rig_profile_ref": v.rig_profile_ref,
                "catalog_ref": v.catalog_ref, "attach_point": v.attach_point, "attach_points": list(v.attach_points),
                "fit_profiles": list(v.fit_profiles), "conflicts_with_slots": list(v.conflicts_with_slots),
                "blender_version": v.blender_version, "draft_report": v.check_report,
                "reviewed_by": str(v.reviewed_by) if v.reviewed_by else None,
                "reviewed_at": v.reviewed_at.isoformat() if v.reviewed_at else None, "review_notes": v.review_notes,
                "based_on": ({"version_id": str(based_on.id), "version": based_on.version_label}
                             if based_on else None),
                "files": [{"name": f.storage_key.rsplit("/", 1)[-1], "role": f.role, "storage_key": f.storage_key,
                           "sha256": f.sha256, "size_bytes": f.size_bytes, "mime_type": f.mime_type}
                          for f in sorted(v.entry.files, key=lambda f: f.storage_key)],
            }

    @staticmethod
    def _accessory_manifest(snap: dict[str, Any], major: int, minor: int, files: list[dict[str, Any]],
                            report: dict[str, Any]) -> dict[str, Any]:
        """AssetVersionManifest for an accessory, with the compatibility metadata a scene
        checks before it pins the version."""
        draft = snap["draft_report"] or {}
        return {
            "manifest_version": "asset_manifest/1",
            "kind": "accessory", "code": snap["code"], "accessory_id": snap["accessory_id"],
            "version": f"{major}.{minor}", "version_id": snap["version_id"], "name": snap["name"],
            "based_on": snap["based_on"], "category": snap["category"], "item": snap["item_ref"],
            "files": [{"role": f["role"], "key": f["storage_key"].rsplit("/", 1)[-1], "sha256": f["sha256"],
                       "size": f["size_bytes"], "mime": f["mime_type"]}
                      for f in sorted(files, key=lambda f: f["storage_key"])],
            "dependencies": [{"kind": "rig_profile", "ref": snap["rig_profile_ref"]},
                             {"kind": "style_profile", "ref": snap["style_profile_ref"]},
                             {"kind": "accessory_catalog", "ref": snap["catalog_ref"]}],
            "compatibility": {"rig_profile": snap["rig_profile_ref"], "attach_point": snap["attach_point"],
                              "attach_points": snap["attach_points"], "fit_profiles": snap["fit_profiles"],
                              "conflicts_with_slots": snap["conflicts_with_slots"]},
            "spec_digest": snap["spec_digest"],
            "blender_version": snap["blender_version"], "engine_version": draft.get("engine_version"),
            "validation": {"build": {"ok": draft.get("ok"), "checks": len(draft.get("checks", []))},
                           "publish": {"ok": report["ok"], "checks": len(report.get("checks", [])),
                                       "blender_version": report.get("blender_version")}},
            "approved_by": snap["reviewed_by"], "approved_at": snap["reviewed_at"],
        }
