"""Phase 1: parametric characters -> library characters."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from studio.schemas import Characters, Manifest

log = logging.getLogger("studio.assets")


def library_id_for(character_id: str) -> str:
    slug = character_id[len("char_") :] if character_id.startswith("char_") else character_id
    return f"lib_char_{slug}"


def plan_character_builds(characters: Characters, manifest: Manifest) -> list[dict[str, Any]]:
    """One entry per parametric sheet: what to build it from and whether the base exists."""
    bases = manifest.base_by_id()
    existing = manifest.character_by_id()
    plan = []
    for ch in characters.characters:
        a = ch.asset
        if a.status != "parametric":
            continue
        lib_id = library_id_for(ch.id)
        plan.append(
            {
                "character_id": ch.id,
                "base_id": a.base_id,
                "base_exists": a.base_id in bases,
                "library_id": lib_id,
                "already_built": lib_id in existing,
                "outfit_id": ch.default_outfit_id,
                "garments": sorted({s.garment_id for o in ch.outfits for s in o.slots.values() if s.garment_id}),
                "hair_id": a.hair_id,
            }
        )
    return plan


def promote_built(characters: Characters, records: list[dict[str, Any]]) -> Characters:
    """After a build, the sheet points at the library asset the build produced. The
    parametric fields stay on the sheet so the character can be rebuilt from them."""
    by_char = {r["character_id"]: r for r in records}
    data = characters.model_dump(mode="json")
    for ch in data["characters"]:
        rec = by_char.get(ch["id"])
        if rec is None:
            continue
        ch["asset"]["status"] = "library"
        ch["asset"]["library_id"] = rec["library_id"]
    return Characters.model_validate(data)


def build_characters(
    library: Path,
    characters: Characters,
    only: list[str] | None = None,
    toon: bool = False,
    rescan: bool = True,
) -> tuple[list[dict[str, Any]], Characters]:
    """Run the Blender builder (needs ``bpy``), rescan the manifest, return the records and
    the promoted sheets."""
    try:
        from tools.build_character import build_all
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError("tools.build_character needs the bpy module or a Blender run") from exc
    records = build_all(library, characters.model_dump(mode="json"), only=only, toon=toon)
    if rescan and records:
        from tools.scan_library import main as scan_main

        scan_main(["--library", str(library)])
    return records, promote_built(characters, records)


def write_build_report(run_dir: Path, name: str, records: list[dict[str, Any]]) -> Path:
    path = run_dir / "assets" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2) + "\n")
    return path
