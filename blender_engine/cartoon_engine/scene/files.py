"""Checks on saved scene files (architecture 7.1): every library a scene links must be a
relative path into the workspace's ``assets/`` tree and one of the files the manifest pins;
nothing absolute, nothing outside. And the post-condition of every scene job: the staged
source files hash exactly as before.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from studio_contracts.scene import ResolvedScene


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_files(rs: ResolvedScene) -> dict[str, str]:
    """workspace-relative path -> SHA-256 of every pinned file and dialogue recording."""
    out = {f"{p.path}/{name}": sha for p in rs.pins() for name, sha in p.files.items()}
    out.update({a.path: a.sha256 for a in rs.audio()})
    return out


def hash_sources(ws: Path, rs: ResolvedScene) -> dict[str, str]:
    return {rel: sha256(ws / rel) for rel in expected_files(rs)}


def unchanged(ws: Path, rs: ResolvedScene) -> tuple[bool, list[str]]:
    exp = expected_files(rs)
    now = hash_sources(ws, rs)
    changed = sorted(rel for rel, sha in exp.items() if now.get(rel) != sha)
    return not changed, changed


def library_check(blend_dir: Path, ws: Path, rs: ResolvedScene) -> dict:
    """Libraries of the open file, as stored and as resolved from ``blend_dir``."""
    import bpy

    allowed = {f"{p.path}/{name}" for p in rs.pins() for name in p.files if name.endswith(".blend")}
    root = ws.resolve()
    libs, bad = [], []
    for lib in bpy.data.libraries:
        stored = lib.filepath
        rel = None
        if stored.startswith("//"):
            target = (blend_dir / stored[2:]).resolve()
            try:
                rel = target.relative_to(root).as_posix()
            except ValueError:
                rel = None
        ok = stored.startswith("//") and rel in allowed and (blend_dir / stored[2:]).exists()
        libs.append({"stored": stored, "workspace_path": rel, "ok": ok})
        if not ok:
            bad.append(stored)
    used = {x["workspace_path"] for x in libs}
    return {"libraries": libs, "ok": not bad, "outside_or_absolute": bad,
            "pinned_but_not_linked": sorted(p for p in allowed if p not in used and "/outfit/" not in p and "/animation/" not in p)}
