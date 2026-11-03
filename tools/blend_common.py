"""Helpers shared by the Blender tools. Imports bpy lazily so the module can be imported
without Blender for argument parsing and tests of the pure functions."""

from __future__ import annotations

import sys
from pathlib import Path

KINDS = {
    "characters": "character",
    "props": "prop",
    "envs": "environment",
    "clips/baked": "clip",
    "bases": "base",
    "garments": "garment",
    "hair": "hair",
    "kits": "kit_piece",
    "generators": "generator",
}


def script_args(argv: list[str] | None = None) -> list[str]:
    """Script arguments: everything after ``--`` when run inside Blender, else sys.argv[1:].

    An explicit ``argv`` is treated as already stripped of the program name.
    """
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return argv


def kind_from_path(library: Path, file: Path) -> str | None:
    rel = file.relative_to(library).as_posix()
    for folder, kind in KINDS.items():
        if rel.startswith(folder + "/"):
            return kind
    return None


def split_csv(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def parse_params(value) -> dict[str, tuple[float, float]]:
    """'height_m:0:10,roughness:0:1' -> {'height_m': (0, 10), 'roughness': (0, 1)}"""
    out: dict[str, tuple[float, float]] = {}
    for entry in split_csv(value):
        name, lo, hi = entry.split(":")
        out[name] = (float(lo), float(hi))
    return out


# ---------------------------------------------------------------- bpy helpers


def open_blend(path: Path) -> None:
    import bpy

    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)


def scene_prop(name: str, default=None):
    import bpy

    scene = bpy.context.scene
    return scene.get(name, default)


def mesh_bounds() -> tuple[float, float, float]:
    """Axis-aligned size of all mesh geometry in world space at the current frame."""
    import bpy

    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    found = False
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        found = True
        for corner in obj.bound_box:
            world = obj.matrix_world @ _vec(corner)
            for i in range(3):
                lo[i] = min(lo[i], world[i])
                hi[i] = max(hi[i], world[i])
    if not found:
        return (0.0, 0.0, 0.0)
    return tuple(round(hi[i] - lo[i], 4) for i in range(3))  # type: ignore[return-value]


def _vec(corner):
    from mathutils import Vector

    return Vector(corner)


def shape_key_names() -> list[str]:
    import bpy

    names: list[str] = []
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.data.shape_keys:
            for kb in obj.data.shape_keys.key_blocks:
                if kb.name != "Basis" and kb.name not in names:
                    names.append(kb.name)
    return names


def armature():
    import bpy

    rig = bpy.data.objects.get("rig")
    if rig is not None and rig.type == "ARMATURE":
        return rig
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            return obj
    return None


def anchors() -> list[str]:
    import bpy

    return sorted(o.name[len("anchor_") :] for o in bpy.data.objects if o.type == "EMPTY" and o.name.startswith("anchor_"))


def time_variant_collections() -> list[str]:
    import bpy

    return sorted(c.name[len("time_") :] for c in bpy.data.collections if c.name.startswith("time_"))


def single_action():
    import bpy

    actions = list(bpy.data.actions)
    return actions[0] if len(actions) == 1 else None


def action_fcurves(action):
    """F-curves of an action across Blender versions (legacy flat list, or 4.4+ layered actions)."""
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        return list(legacy)
    curves = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                curves.extend(bag.fcurves)
    return curves


def root_motion(action, bone: str = "root") -> tuple[float, float, float]:
    """Displacement of the root bone's location between the first and last keyframe."""
    path = f'pose.bones["{bone}"].location'
    deltas = [0.0, 0.0, 0.0]
    found = False
    for fc in action_fcurves(action):
        if fc.data_path == path and fc.keyframe_points:
            found = True
            pts = fc.keyframe_points
            deltas[fc.array_index] = pts[-1].co[1] - pts[0].co[1]
    return tuple(round(d, 4) for d in deltas) if found else (0.0, 0.0, 0.0)  # type: ignore[return-value]
