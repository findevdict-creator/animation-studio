"""Outfit previews: a thumbnail of the wearer, a labelled sheet of the fit-check poses and a
try-on GLB for the browser. Made in the job session after the files are saved; nothing here
goes back into the outfit file."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1

from cartoon_engine.character import previews as P
from cartoon_engine.character.validate import Parts, _reset_pose, _update
from cartoon_engine.outfit.check import POSES, pose, set_fk
from cartoon_engine.outfit.fit import MASK_PREFIX


def _label(scene, size: float):
    import bpy

    data = bpy.data.curves.new("label", "FONT")
    data.align_x = "CENTER"
    data.size = size
    obj = bpy.data.objects.new("label", data)
    scene.collection.objects.link(obj)
    mat = bpy.data.materials.new("label")
    mat.diffuse_color = (0.05, 0.05, 0.08, 1)
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.03, 0.03, 0.05, 1)
    data.materials.append(mat)
    return obj


def thumbnail(parts: Parts, garments: list, out: Path, opts: P.RenderOptions, angle: float = 30.0) -> Path:
    scene, cam = P.setup_scene(opts)
    lo, hi = P._bounds([*parts.meshes(), *garments])
    P._frame(cam, lo, hi, angle)
    P._render_png(scene, out, opts.resolution, opts.resolution)
    return out


def pose_sheet(parts: Parts, garments: list, out: Path, opts: P.RenderOptions, poses: tuple[str, ...],
               tile: int = 256) -> dict:
    """One tile per pose, 3/4 front view, the pose name underneath."""
    import bpy

    scene, cam = P.setup_scene(opts)
    lo, hi = P._bounds([*parts.meshes(), *garments])
    h = float(hi[2] - lo[2])
    lo, hi = lo.copy(), hi.copy()
    lo[2] -= 0.12 * h
    hi[2] += 0.3 * h  # arms up
    lo[1] -= 0.25 * h  # legs forward when sitting
    P._frame(cam, lo, hi, 35.0, margin=1.0, elevation_deg=8.0)
    label = _label(scene, h * 0.09)
    label.location = ((lo[0] + hi[0]) / 2, float(lo[1]), float(lo[2]) + 0.02 * h)
    label.rotation_euler = (math.radians(90), 0, cam.rotation_euler[2])
    tiles = []
    switches = set_fk(parts, True)
    tmp = out.parent / "pose_tiles"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for i, name in enumerate(poses):
            pose(parts, name)
            label.data.body = name.replace("_", " ")
            p = tmp / f"{i:02d}.png"
            P._render_png(scene, p, tile, tile)
            tiles.append(P._load(p))
    finally:
        _reset_pose(parts, CARTOON_HUMANOID_V1)
        set_fk(parts, switches)
        bpy.data.objects.remove(label)
    cols = min(len(tiles), 5 if len(tiles) > 8 else 4)
    rows = math.ceil(len(tiles) / cols)
    blank = np.ones_like(tiles[0])
    tiles += [blank] * (rows * cols - len(tiles))
    grid = [np.concatenate(tiles[r * cols:(r + 1) * cols], axis=1) for r in range(rows)]
    P._save(np.concatenate(list(reversed(grid)), axis=0), out)
    return {"path": out, "poses": list(poses)}


def export_tryon_glb(parts: Parts, garments: list, path: Path) -> dict:
    """The character wearing the outfit: fabric thickness and the body mask are applied to
    the meshes (rest pose, so the result is exact), skinning and face morphs are kept."""
    import bpy

    _reset_pose(parts, CARTOON_HUMANOID_V1)
    _update(parts)
    view = bpy.context.view_layer
    for obj, names in [(g, [m.name for m in g.modifiers if m.type == "SOLIDIFY"]) for g in garments] + \
            [(parts.body, [m.name for m in parts.body.modifiers if m.name.startswith(MASK_PREFIX)])]:
        for name in names:
            view.objects.active = obj
            with bpy.context.temp_override(object=obj, active_object=obj):
                bpy.ops.object.modifier_apply(modifier=name)
    for o in view.objects:
        o.select_set(False)
    for o in [parts.rig, *parts.meshes(), *garments]:
        o.hide_set(False)
        o.select_set(True)
    view.objects.active = parts.rig
    bpy.ops.export_scene.gltf(
        filepath=str(path), export_format="GLB", use_selection=True, export_apply=False,
        export_morph=True, export_morph_normal=False, export_skins=True, export_def_bones=True,
        export_animations=False, export_yup=True, export_materials="EXPORT", export_cameras=False,
        export_lights=False, export_extras=False,
    )
    return P.inspect_glb(path)


__all__ = ["POSES", "export_tryon_glb", "pose_sheet", "thumbnail"]
