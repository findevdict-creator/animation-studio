"""Phase 3: kit compositions -> library environments."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from studio.schemas import Manifest, World


def library_id_for(location_id: str) -> str:
    slug = location_id[len("loc_") :] if location_id.startswith("loc_") else location_id
    return f"lib_env_{slug}"


def plan_environment_builds(world: World, manifest: Manifest) -> list[dict[str, Any]]:
    kits = {k.id: {p.id for p in k.pieces} for k in manifest.kits}
    gens = {g.id for g in manifest.generators}
    existing = manifest.environment_by_id()
    plan = []
    for loc in world.locations:
        a = loc.asset
        if a.status != "composed":
            continue
        pieces = {p.piece_id for p in a.pieces} | {pid for s in a.scatter for pid in s.piece_ids}
        generators = {s.generator_id for s in a.scatter} | ({a.terrain.generator_id} if a.terrain else set())
        plan.append(
            {
                "location_id": loc.id,
                "kit_id": a.kit_id,
                "kit_exists": a.kit_id in kits,
                "missing_pieces": sorted(pieces - kits.get(a.kit_id or "", set())),
                "missing_generators": sorted(generators - gens),
                "library_id": library_id_for(loc.id),
                "already_built": library_id_for(loc.id) in existing,
                "seed": a.seed,
                "lighting_preset": a.lighting_preset,
            }
        )
    return plan


def promote_built(world: World, records: list[dict[str, Any]]) -> World:
    """The location points at the environment the build produced; the composition stays so
    the set can be rebuilt. ``time_variant`` is set from the lighting preset when unset."""
    by_loc = {r["location_id"]: r for r in records}
    data = world.model_dump(mode="json")
    for loc in data["locations"]:
        rec = by_loc.get(loc["id"])
        if rec is None:
            continue
        loc["asset"]["status"] = "library"
        loc["asset"]["library_id"] = rec["library_id"]
        if not loc["asset"].get("time_variant"):
            preset = loc["asset"].get("lighting_preset")
            if preset in (rec.get("time_variants") or []):
                loc["asset"]["time_variant"] = preset
    return World.model_validate(data)


def build_environments(library: Path, world: World, only: list[str] | None = None, rescan: bool = True) -> tuple[list[dict[str, Any]], World]:
    try:
        from tools.compose_env import compose_all
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("tools.compose_env needs the bpy module or a Blender run") from exc
    records = compose_all(library, world.model_dump(mode="json"), only=only)
    if rescan and records:
        from tools.scan_library import main as scan_main

        scan_main(["--library", str(library)])
    return records, promote_built(world, records)
