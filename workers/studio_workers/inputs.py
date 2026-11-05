"""Input files a job type needs in its workspace before Blender starts (architecture 11.1).

The Blender process never reads storage or the database, so the worker materialises the
files it needs under ``inputs/`` and lists them in the job envelope with their SHA-256.
Every file is checked against the SHA-256 recorded on its asset row while it downloads;
a mismatch is permanent (the stored object is not the file the row describes).

Scene jobs stage every pinned file at ``assets/<kind>/<code>/<version>/`` (the paths the
saved ``scene.blend`` links through) and make them read-only; ``verify_scene`` also gets
the stored ``scene.blend`` at ``scene/scene.blend``. Each staged file is checked against
both the registry row and the hash frozen in the scene version's resolution.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.assets import outfits as outfit_svc
from app.config import Settings
from app.core.storage import Storage
from app.models.characters import CharacterVersion
from app.models.outfits import OutfitVersion
from sqlalchemy.orm import Session
from studio_contracts.jobs import InputFile


class InputRefused(Exception):
    """Permanent: the inputs cannot be staged (missing version, checksum mismatch, …)."""


Wanted = list[tuple[str, str, str, str]]  # (bucket, storage key, sha256, workspace path)
Stager = Callable[[Session, dict[str, Any]], Wanted]


def _bucket(status: str) -> str:
    return "library" if status in ("published", "deprecated") else "work"


def _pick(files: dict[str, Any], name: str, what: str) -> Any:
    f = files.get(name)
    if f is None:
        raise InputRefused(f"{what} has no {name}")
    return f


def _fit_outfit(session: Session, params: dict[str, Any]) -> Wanted:
    ov = session.get(OutfitVersion, uuid.UUID(params["outfit_version_id"]))
    cv = session.get(CharacterVersion, uuid.UUID(params["character_version_id"]))
    if ov is None or cv is None:
        raise InputRefused("the outfit or character version no longer exists")
    o_files = outfit_svc.files_by_name(ov)
    c_files = {f.storage_key.rsplit("/", 1)[-1]: f for f in cv.entry.files}
    blend = _pick(o_files, "outfit.blend", f"outfit version {ov.id}")
    cblend = _pick(c_files, "character.blend", f"character version {cv.id}")
    cjson = _pick(c_files, "character.json", f"character version {cv.id}")
    return [(_bucket(cv.status), cblend.storage_key, cblend.sha256, "inputs/character.blend"),
            (_bucket(cv.status), cjson.storage_key, cjson.sha256, "inputs/character.json"),
            (_bucket(ov.status), blend.storage_key, blend.sha256, "inputs/outfit.blend")]


def _scene_pins(session: Session, params: dict[str, Any]) -> Wanted:
    from app.assets import scenes as scene_svc
    from studio_contracts.scene import ResolvedScene

    rs = ResolvedScene.model_validate(params["resolved"])
    wanted: Wanted = []
    for p in rs.pins():
        v = session.get(scene_svc.MODELS[p.kind], uuid.UUID(p.version_id))
        if v is None:
            raise InputRefused(f"{p.code}@{p.version} no longer exists")
        files = {f.storage_key.rsplit("/", 1)[-1]: f for f in v.entry.files}
        for name, sha in p.files.items():
            f = _pick(files, name, f"{p.code}@{p.version}")
            if f.sha256 != sha:
                raise InputRefused(f"{p.code}@{p.version} {name} is not the pinned file")
            wanted.append((_bucket(v.status), f.storage_key, sha, f"{p.path}/{name}"))
    return wanted


def _verify_scene(session: Session, params: dict[str, Any]) -> Wanted:
    from app.assets import scenes as scene_svc
    from app.models.scenes import SceneVersion

    v = session.get(SceneVersion, uuid.UUID(params["scene_version_id"]))
    blend = scene_svc.scene_blend(v) if v is not None else None
    if blend is None or blend["sha256"] != params["scene_blend_sha256"]:
        raise InputRefused("the scene version has no stored scene.blend with that SHA-256")
    return [*_scene_pins(session, params), ("work", blend["storage_key"], blend["sha256"], "scene/scene.blend")]


def _generate_animation(session: Session, params: dict[str, Any]) -> Wanted:
    """A BVH take is staged as ``inputs/source.bvh`` from its upload, checked against the
    SHA-256 the specification names; a generator needs no input."""
    from app.models.uploads import Upload
    from sqlalchemy import select

    src = params["spec"]["source"]
    if src.get("kind") != "bvh":
        return []
    up = session.scalar(select(Upload).where(Upload.storage_key == src["upload_key"]))
    if up is None or up.sha256 != src["sha256"]:
        raise InputRefused(f"no upload {src['upload_key']} with SHA-256 {src['sha256']}")
    return [("work", up.storage_key, up.sha256, "inputs/source.bvh")]


STAGERS: dict[str, Stager] = {"fit_outfit": _fit_outfit, "assemble_scene": _scene_pins, "verify_scene": _verify_scene,
                              "generate_animation": _generate_animation}
READ_ONLY_PREFIXES = ("assets/",)


def stage(session: Session, job_type: str, params: dict[str, Any], workspace: Path, settings: Settings,
          storages: dict[str, Storage] | None = None) -> list[InputFile]:
    """Download the inputs of ``job_type`` into ``workspace``; [] for a type that needs none."""
    from app.core.storage import ChecksumMismatch

    stager = STAGERS.get(job_type)
    if stager is None:
        return []
    storages = storages if storages is not None else {}
    out = []
    for bucket, key, sha, rel in stager(session, params):
        store = storages.get(bucket) or storages.setdefault(bucket, Storage.for_bucket(bucket, settings))
        try:
            store.get_file(key, workspace / rel, expected_sha256=sha)
        except ChecksumMismatch as exc:
            raise InputRefused(f"{rel} does not match its recorded SHA-256: {exc}") from exc
        if rel.startswith(READ_ONLY_PREFIXES):
            (workspace / rel).chmod(0o444)
        out.append(InputFile(path=rel, sha256=sha))
    return out
