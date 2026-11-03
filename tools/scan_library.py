"""Scan the asset library into manifest.json.

    blender -b --python tools/scan_library.py -- --library library --out library/manifest.json
    python -m tools.scan_library --library library --out library/manifest.json   (bpy wheel)

Reads each .blend once, following library/CONVENTIONS.md, and writes the manifest the
stage 1 agents and checks consume. Validates against studio.schemas.Manifest when the
``studio`` package is importable.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.blend_common import (
    anchors,
    armature,
    kind_from_path,
    mesh_bounds,
    open_blend,
    parse_params,
    root_motion,
    scene_prop,
    script_args,
    shape_key_names,
    single_action,
    split_csv,
    time_variant_collections,
)

SECTIONS = {
    "character": "characters",
    "prop": "props",
    "environment": "environments",
    "clip": "clips",
    "base": "bases",
    "garment": "garments",
    "hair": "hair",
    "generator": "generators",
}


def library_version(library: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=library, capture_output=True, text=True, check=True,
        ).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.CalledProcessError):
        pass
    return datetime.now(timezone.utc).strftime("%Y.%m.%d")


def scan_file(library: Path, file: Path) -> tuple[str, dict] | None:
    kind = kind_from_path(library, file)
    if kind is None:
        return None
    open_blend(file)
    asset_id = file.stem
    declared = scene_prop("studio_kind")
    if declared and declared != kind:
        raise ValueError(f"{file}: studio_kind {declared!r} but folder says {kind!r}")
    tags = split_csv(scene_prop("studio_tags"))
    rel = file.relative_to(library).as_posix()

    if kind == "character":
        rig = armature()
        bounds = mesh_bounds()
        return kind, {
            "id": asset_id,
            "file": rel,
            "rig_type": scene_prop("studio_rig_type", "custom" if rig else "custom"),
            "height_m": bounds[2],
            "outfit_slots": split_csv(scene_prop("studio_outfit_slots")),
            "shape_keys": shape_key_names(),
            "tags": tags,
        }
    if kind == "prop":
        return kind, {
            "id": asset_id,
            "file": rel,
            "bounds_m": list(mesh_bounds()),
            "attach_bone": scene_prop("studio_attach_bone"),
            "tags": tags,
        }
    if kind == "environment":
        variants = split_csv(scene_prop("studio_time_variants")) or time_variant_collections()
        return kind, {
            "id": asset_id,
            "file": rel,
            "bounds_m": list(mesh_bounds()),
            "anchors": anchors(),
            "time_variants": variants,
        }
    if kind == "clip":
        action = single_action()
        if action is None:
            raise ValueError(f"{file}: a clip file must contain exactly one Action")
        start, end = action.frame_range
        return kind, {
            "id": asset_id,
            "rig_type": scene_prop("studio_rig_type", file.parent.name),
            "frames": int(round(end - start)) + 1,
            "loop": bool(int(scene_prop("studio_loop", 0))),
            "root_motion_m": list(root_motion(action)),
            "tags": tags,
            "source": scene_prop("studio_source"),
            "licence": scene_prop("studio_licence"),
        }
    if kind == "base":
        return kind, {
            "id": asset_id,
            "rig_type": scene_prop("studio_rig_type", "custom"),
            "proportion_sliders": split_csv(scene_prop("studio_proportion_sliders")),
            "material_slots": split_csv(scene_prop("studio_material_slots")),
            "outfit_slots": split_csv(scene_prop("studio_outfit_slots")),
            "shape_keys": [k for k in shape_key_names() if k not in split_csv(scene_prop("studio_proportion_sliders"))],
        }
    if kind == "garment":
        return kind, {
            "id": asset_id,
            "slot": scene_prop("studio_slot", ""),
            "fits": split_csv(scene_prop("studio_fits")),
            "tags": tags,
        }
    if kind == "hair":
        return kind, {"id": asset_id, "fits": split_csv(scene_prop("studio_fits"))}
    if kind == "kit_piece":
        bounds = mesh_bounds()
        return kind, {
            "kit": scene_prop("studio_kit", file.parent.name),
            "piece": {
                "id": asset_id,
                "footprint_m": [bounds[0], bounds[1]],
                "snap_points": split_csv(scene_prop("studio_snap_points")),
                "tags": tags,
            },
        }
    if kind == "generator":
        return kind, {
            "id": asset_id,
            "kind": scene_prop("studio_generator_kind", "prop"),
            "params": {k: list(v) for k, v in parse_params(scene_prop("studio_params")).items()},
        }
    return None


def scan(library: Path) -> dict:
    manifest: dict = {
        "library_version": library_version(library),
        "units": "metres",
        "characters": [], "props": [], "environments": [], "clips": [],
        "camera_rigs": ["static", "dolly", "crane", "handheld_noise"],
        "bases": [], "garments": [], "hair": [], "kits": [], "generators": [],
        "lighting_presets": [],
    }
    kits: dict[str, list[dict]] = {}
    for file in sorted(library.rglob("*.blend")):
        if "preview" in file.parts or "source" in file.parts:
            continue
        result = scan_file(library, file)
        if result is None:
            continue
        kind, entry = result
        if kind == "kit_piece":
            kits.setdefault(entry["kit"], []).append(entry["piece"])
        else:
            manifest[SECTIONS[kind]].append(entry)
    manifest["kits"] = [{"id": kit_id, "pieces": pieces} for kit_id, pieces in sorted(kits.items())]
    presets = library / "lighting_presets.json"
    if presets.exists():
        manifest["lighting_presets"] = json.loads(presets.read_text())
    return manifest


def validate(manifest: dict) -> dict:
    try:
        from studio.schemas import Manifest
    except ImportError:
        print("studio package not importable from Blender's Python; skipping schema validation", file=sys.stderr)
        return manifest
    return Manifest.model_validate(manifest).model_dump(mode="json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="library")
    parser.add_argument("--out", default=None, help="defaults to <library>/manifest.json")
    args = parser.parse_args(script_args(argv))
    library = Path(args.library).resolve()
    out = Path(args.out) if args.out else library / "manifest.json"
    manifest = validate(scan(library))
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    counts = {k: len(v) for k, v in manifest.items() if isinstance(v, list) and v}
    print(f"wrote {out}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
