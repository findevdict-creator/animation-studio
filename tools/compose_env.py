"""Compose an environment .blend from a kit composition (asset build phase 3).

    blender -b --python tools/compose_env.py -- --library library --world runs/x/current/world.json [--only loc_forest_clearing] [--rescan]
    python -m tools.compose_env --library library --world world.json           (bpy wheel)

For every location whose ``asset.status`` is ``composed`` this builds the set from the
Scene Builder's composition: a terrain generated from the seed, the kit pieces the
anchors depend on placed where the composition says, scatter generators filling the rest
deterministically from the same seed, anchor empties, a light rig per time-of-day variant,
and the library custom properties. Saves ``library/envs/lib_env_<slug>.blend``; the file
scans like a hand-built environment. Same composition, same seed, same set on every
machine: all randomness goes through ``random.Random(seed)`` and ``mathutils.noise``.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

from tools.blend_common import mesh_bounds, open_blend, script_args, split_csv

__all__ = ["compose_environment", "compose_all", "library_id_for", "scatter_points", "terrain_height", "main"]

LIGHT_RIGS: dict[str, list[dict[str, Any]]] = {
    "day": [{"type": "SUN", "energy": 4.0, "color": (1.0, 0.98, 0.92), "rotation": (50, 0, 30)}],
    "dusk": [{"type": "SUN", "energy": 1.5, "color": (1.0, 0.7, 0.45), "rotation": (80, 0, 60)}],
    "night": [{"type": "SUN", "energy": 0.2, "color": (0.6, 0.7, 1.0), "rotation": (40, 0, 200)}],
    "interior_warm": [
        {"type": "AREA", "energy": 400.0, "color": (1.0, 0.85, 0.7), "location": (0, 0, 2.6), "size": 2.0},
        {"type": "POINT", "energy": 60.0, "color": (1.0, 0.8, 0.6), "location": (1.5, 1.0, 1.2)},
    ],
}
EXTERIOR_VARIANTS = ["day", "dusk", "night"]


# ------------------------------------------------------------- pure helpers
def library_id_for(location_id: str) -> str:
    """loc_forest_clearing -> lib_env_forest_clearing"""
    slug = location_id[len("loc_") :] if location_id.startswith("loc_") else location_id
    return f"lib_env_{slug}"


def terrain_height(x: float, y: float, seed: int, height_m: float, roughness: float) -> float:
    """Deterministic rolling terrain: two octaves of value noise, zero mean at the origin."""
    if height_m <= 0:
        return 0.0
    try:
        from mathutils import noise

        base = 40.0 / (1.0 + 3.0 * roughness)  # rougher terrain varies over shorter distances
        offset = (seed % 1000) * 7.31
        n1 = noise.noise((x / base + offset, y / base + offset, 0.37))
        n2 = noise.noise((x / (base / 3) + offset, y / (base / 3) + offset, 1.91)) * 0.35 * roughness
        origin = noise.noise((offset, offset, 0.37)) + noise.noise((offset, offset, 1.91)) * 0.35 * roughness
        return (n1 + n2 - origin) * height_m
    except ImportError:  # pure-python fallback keeps the function usable in tests
        rng = random.Random(f"{seed}:{round(x)}:{round(y)}")
        return (rng.random() - 0.5) * height_m * roughness


def scatter_points(
    seed: int,
    index: int,
    bounds: tuple[float, float, float],
    density: float,
    exclude_radius: float,
    exclusions: list[tuple[float, float]],
    ring: dict[str, float] | None = None,
    per_100m2: float = 6.0,
) -> list[tuple[float, float, float, float]]:
    """(x, y, rotation_deg, scale) for a scatter layer. ``density`` 1.0 means ``per_100m2``
    instances per 100 square metres of the covered area; the ring limits placement to an
    annulus around the origin. Deterministic in (seed, index)."""
    rng = random.Random(seed * 1000 + index)
    hx, hy = bounds[0] / 2, bounds[1] / 2
    if ring:
        inner, outer = float(ring.get("inner_m", 0.0)), float(ring.get("outer_m", min(hx, hy)))
        area = math.pi * (outer**2 - inner**2)
    else:
        inner, outer = 0.0, 0.0
        area = bounds[0] * bounds[1]
    count = int(round(density * per_100m2 * area / 100.0))
    out: list[tuple[float, float, float, float]] = []
    attempts = 0
    while len(out) < count and attempts < count * 20 + 50:
        attempts += 1
        if ring:
            r = math.sqrt(rng.uniform(inner**2, outer**2))
            a = rng.uniform(0, 2 * math.pi)
            x, y = r * math.cos(a), r * math.sin(a)
            if abs(x) > hx or abs(y) > hy:
                continue
        else:
            x, y = rng.uniform(-hx, hx), rng.uniform(-hy, hy)
        if any((x - ex) ** 2 + (y - ey) ** 2 < exclude_radius**2 for ex, ey in exclusions):
            continue
        out.append((round(x, 3), round(y, 3), round(rng.uniform(0, 360), 1), round(rng.uniform(0.8, 1.25), 3)))
    return out


# -------------------------------------------------------------- bpy helpers
def _reset():
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.render.fps = 24
    return scene


def _append_meshes(path: Path, into) -> list:
    import bpy

    with bpy.data.libraries.load(str(path), link=False) as (src, dst):
        dst.objects = [n for n in src.objects]
    added = []
    for obj in dst.objects:
        if obj is not None and obj.type == "MESH":
            into.objects.link(obj)
            added.append(obj)
        elif obj is not None:
            bpy.data.objects.remove(obj)
    return added


def _bake_transform(obj, position, rotation_deg: float, scale: float) -> None:
    """Apply rotation and scale to the mesh data (lint wants scale 1) and move the object."""
    import mathutils

    rot = mathutils.Matrix.Rotation(math.radians(rotation_deg), 4, "Z")
    for v in obj.data.vertices:
        v.co = rot @ (v.co * scale)
    obj.location = position


def _ground(bounds, seed: int, terrain: dict[str, Any] | None, collection):
    import bpy

    size_x, size_y = bounds[0], bounds[1]
    cells = 48 if terrain else 1
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=cells, y_subdivisions=cells, size=1.0)
    ground = bpy.context.active_object
    ground.name = "ground"
    for v in ground.data.vertices:
        v.co.x *= size_x
        v.co.y *= size_y
    params = (terrain or {}).get("params", {})
    height, rough = float(params.get("height_m", 0.0)), float(params.get("roughness", 0.3))
    if terrain:
        for v in ground.data.vertices:
            v.co.z = terrain_height(v.co.x, v.co.y, seed, height, rough)
    for c in ground.users_collection:
        c.objects.unlink(ground)
    collection.objects.link(ground)
    mat = bpy.data.materials.new("ground")
    mat.diffuse_color = (0.25, 0.4, 0.2, 1.0)
    ground.data.materials.append(mat)
    return ground, (lambda x, y: terrain_height(x, y, seed, height, rough) if terrain else 0.0)


def _lights(variants: list[str], preset: str | None, scene) -> None:
    import bpy

    for variant in variants:
        coll = bpy.data.collections.new(f"time_{variant}")
        scene.collection.children.link(coll)
        for n, spec in enumerate(LIGHT_RIGS.get(variant, LIGHT_RIGS["day"])):
            data = bpy.data.lights.new(f"{variant}_{spec['type'].lower()}_{n}", spec["type"])
            data.energy = spec["energy"]
            data.color = spec["color"]
            if spec["type"] == "AREA":
                data.size = spec.get("size", 1.0)
            light = bpy.data.objects.new(data.name, data)
            if "rotation" in spec:
                light.rotation_euler = tuple(math.radians(a) for a in spec["rotation"])
            if "location" in spec:
                light.location = spec["location"]
            coll.objects.link(light)
        coll.hide_render = coll.hide_viewport = bool(preset) and variant != preset


# ---------------------------------------------------------------- the build
def compose_environment(library: Path, location: dict[str, Any], out_dir: Path | None = None) -> dict[str, Any]:
    import bpy

    asset = location["asset"]
    if asset.get("status") != "composed":
        raise ValueError(f"{location['id']}: asset.status is {asset.get('status')!r}, not 'composed'")
    kit_id = asset["kit_id"]
    kit_dir = library / "kits" / kit_id.removeprefix("kit_")
    if not kit_dir.is_dir():
        raise FileNotFoundError(f"{location['id']}: kit {kit_id} not found at {kit_dir}")
    seed = int(asset.get("seed") or 0)
    bounds = tuple(asset.get("bounds_m") or (40.0, 40.0, 15.0))
    interior = location.get("setting_type") == "interior"
    record: dict[str, Any] = {"location_id": location["id"], "kit_id": kit_id, "seed": seed, "warnings": [], "pieces": 0, "scatter": {}}

    scene = _reset()
    sets = bpy.data.collections.new("set")
    scene.collection.children.link(sets)
    ground, height_at = _ground(bounds, seed, asset.get("terrain"), sets)

    # Placed pieces (the ones anchors depend on). Each piece's mesh is appended once.
    sources: dict[str, Any] = {}

    def piece_copy(piece_id: str):
        if piece_id not in sources:
            file = kit_dir / f"{piece_id}.blend"
            if not file.exists():
                return None
            objs = _append_meshes(file, sets)
            if not objs:
                return None
            src = objs[0]
            src.hide_render = src.hide_viewport = True
            src.name = f"{piece_id}__source"
            sources[piece_id] = src
        src = sources[piece_id]
        copy = src.copy()
        copy.data = src.data.copy()
        copy.hide_render = copy.hide_viewport = False
        sets.objects.link(copy)
        return copy

    occupied: list[tuple[float, float]] = []
    for n, placement in enumerate(asset.get("pieces") or []):
        obj = piece_copy(placement["piece_id"])
        if obj is None:
            record["warnings"].append(f"kit piece {placement['piece_id']} not found")
            continue
        x, y, z = placement["position_m"]
        obj.name = f"{placement['piece_id']}_{n:02d}"
        _bake_transform(obj, (x, y, z + height_at(x, y)), float(placement.get("rotation_deg", 0.0)), float(placement.get("scale", 1.0)))
        occupied.append((x, y))
        record["pieces"] += 1

    anchors = [(a["id"], tuple(a["position_m"]), float(a.get("facing_deg", 0.0))) for a in location.get("anchors", [])]
    exclusions = occupied + [(p[0], p[1]) for _, p, _ in anchors]

    # Scatter layers from the seed.
    for index, layer in enumerate(asset.get("scatter") or []):
        gen_id = layer["generator_id"]
        gen_file = library / "generators" / f"{gen_id}.blend"
        if not gen_file.exists():
            record["warnings"].append(f"generator {gen_id} not found; layer skipped")
            continue
        piece_ids = list(layer.get("piece_ids") or [])
        if not piece_ids:
            objs = _append_meshes(gen_file, sets)
            if not objs:
                record["warnings"].append(f"generator {gen_id} has no mesh and no piece_ids; layer skipped")
                continue
            src = objs[0]
            src.hide_render = src.hide_viewport = True
            src.name = f"{gen_id}__source"
            sources[gen_id] = src
            piece_ids = [gen_id]
        points = scatter_points(seed, index, bounds, float(layer.get("density", 0.5)), float(layer.get("exclude_radius_m", 0.0)), exclusions, layer.get("ring"))
        rng = random.Random(seed * 1000 + index + 500)
        placed = 0
        for x, y, rot, scale in points:
            pid = rng.choice(piece_ids)
            obj = piece_copy(pid) if pid != gen_id else _copy_of(sources[gen_id], sets)
            if obj is None:
                continue
            obj.name = f"{pid}_s{index}_{placed:03d}"
            _bake_transform(obj, (x, y, height_at(x, y)), rot, scale)
            placed += 1
        record["scatter"][gen_id] = placed

    # Anchors as empties, snapped to the terrain.
    for anchor_id, (x, y, z), facing in anchors:
        name = anchor_id[len("anc_") :] if anchor_id.startswith("anc_") else anchor_id
        empty = bpy.data.objects.new(f"anchor_{name}", None)
        empty.location = (x, y, z + height_at(x, y))
        empty.rotation_euler = (0, 0, math.radians(-facing))
        scene.collection.objects.link(empty)
    record["anchors"] = [a[0] for a in anchors]

    # Lights per variant; the composition's preset is the visible one.
    variants = ["interior_warm"] if interior else list(EXTERIOR_VARIANTS)
    preset = asset.get("lighting_preset")
    if preset and preset not in variants:
        variants.append(preset)
    _lights(variants, preset, scene)
    record["time_variants"] = variants

    # Drop the hidden sources so bounds and the scan see only the set.
    for src in sources.values():
        bpy.data.objects.remove(src)

    lib_id = library_id_for(location["id"])
    tags = {kit_id, location.get("setting_type", ""), *(location.get("key_features") or [])[:4]}
    scene["studio_kind"] = "environment"
    scene["studio_time_variants"] = ",".join(variants)
    scene["studio_tags"] = ",".join(sorted(t.replace(" ", "_") for t in tags if t))
    scene["studio_composed_from"] = location["id"]
    scene["studio_kit"] = kit_id
    scene["studio_seed"] = seed
    out = (out_dir or library / "envs") / f"{lib_id}.blend"
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out), compress=True)
    record.update({"library_id": lib_id, "file": out.relative_to(library).as_posix() if out.is_relative_to(library) else str(out), "bounds_m": [round(v, 3) for v in mesh_bounds()]})
    return record


def _copy_of(src, collection):
    copy = src.copy()
    copy.data = src.data.copy()
    copy.hide_render = copy.hide_viewport = False
    collection.objects.link(copy)
    return copy


def composed_entries(world: dict[str, Any]) -> list[dict[str, Any]]:
    return [l for l in world.get("locations", []) if l.get("asset", {}).get("status") == "composed"]


def compose_all(library: Path, world: dict[str, Any], only: list[str] | None = None) -> list[dict[str, Any]]:
    return [compose_environment(library, loc) for loc in composed_entries(world) if not only or loc["id"] in only]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="library")
    parser.add_argument("--world", required=True, help="world.json from a stage 1 run")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--rescan", action="store_true")
    parser.add_argument("--report", default=None)
    args = parser.parse_args(script_args(argv))
    library = Path(args.library).resolve()
    records = compose_all(library, json.loads(Path(args.world).read_text()), only=args.only or None)
    for r in records:
        print(f"composed {r['library_id']} from {r['kit_id']} seed {r['seed']}: {r['pieces']} pieces, scatter {r['scatter']}, anchors {r['anchors']}")
        for w in r["warnings"]:
            print(f"  warning: {w}")
    if args.report:
        Path(args.report).write_text(json.dumps(records, indent=2) + "\n")
    if args.rescan:
        from tools.scan_library import main as scan_main

        scan_main(["--library", str(library)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
