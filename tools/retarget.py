"""Retarget mocap onto a base rig and bake it to a library clip (asset build phase 2).

    blender -b --python tools/retarget.py -- --library library --source library/clips/source/mixamo/walk_03.fbx \\
        --base base_human_child --map tools/bone_maps/mixamo_to_rigify_human.json --name clip_walk_03 [--loop auto] [--licence CC0]
    python -m tools.retarget --library library --source ... (bpy wheel)

Steps: import the source (FBX or .blend) next to the base rig, constrain every mapped
target bone to its source bone (world-space rotation; location for the root), bake the
result into one Action on the base rig, drop the constraints and the source, scale the
root travel to the base's height, start the root at the origin, measure foot sliding,
decide looping, tag the clip and save ``clips/baked/<rig_type>/<name>.blend`` holding only
the rig and the Action. The scanner then reads frames, loop and root motion from it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from tools.blend_common import action_fcurves, open_blend, scene_prop, script_args
from tools.tag_clips import merge_tags, tags_from_motion, tags_from_name

__all__ = ["retarget", "load_bone_map", "foot_slide_cm", "main"]


def load_bone_map(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    for key in ("target_rig_type", "bones"):
        if key not in data:
            raise ValueError(f"{path}: bone map needs '{key}'")
    data.setdefault("root", next(iter(data["bones"])))
    data.setdefault("feet", [])
    return data


# -------------------------------------------------------------- bpy helpers
def _import_source(path: Path) -> tuple[Any, Any]:
    """Returns (source armature object, its action)."""
    import bpy

    before = set(bpy.data.objects)
    if path.suffix.lower() == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path), automatic_bone_orientation=False, ignore_leaf_bones=True)
        new = [o for o in bpy.data.objects if o not in before]
    else:
        with bpy.data.libraries.load(str(path), link=False) as (src, dst):
            dst.objects = [n for n in src.objects]
            dst.actions = [n for n in src.actions]
        new = [o for o in dst.objects if o is not None]
        for obj in new:
            bpy.context.scene.collection.objects.link(obj)
    rigs = [o for o in new for _ in [0] if o.type == "ARMATURE"]
    if not rigs:
        raise ValueError(f"{path}: no armature in the source")
    src_rig = rigs[0]
    action = src_rig.animation_data.action if src_rig.animation_data else None
    if action is None:
        actions = [a for a in bpy.data.actions if a.users == 0 or a.name.startswith(path.stem)]
        if actions:
            action = actions[0]
            src_rig.animation_data_create()
            src_rig.animation_data.action = action
    if action is None:
        raise ValueError(f"{path}: the source armature has no action")
    for obj in new:
        if obj.type == "MESH":
            bpy.data.objects.remove(obj)
    return src_rig, action


def _constrain(target_rig, src_rig, bone_map: dict[str, Any]) -> list[tuple[str, str]]:
    import bpy

    mapped: list[tuple[str, str]] = []
    for src_name, dst_name in bone_map["bones"].items():
        if src_name not in src_rig.pose.bones or dst_name not in target_rig.pose.bones:
            continue
        pb = target_rig.pose.bones[dst_name]
        rot = pb.constraints.new("COPY_ROTATION")
        rot.name = "retarget_rot"
        rot.target = src_rig
        rot.subtarget = src_name
        rot.owner_space = rot.target_space = "WORLD"
        rot.mix_mode = "REPLACE"
        if src_name == bone_map["root"]:
            loc = pb.constraints.new("COPY_LOCATION")
            loc.name = "retarget_loc"
            loc.target = src_rig
            loc.subtarget = src_name
            loc.owner_space = loc.target_space = "WORLD"
            loc.use_z = False  # hips ride at hip height; the root stays on the ground
        mapped.append((src_name, dst_name))
    bpy.context.view_layer.update()
    return mapped


def _bake(target_rig, frame_start: int, frame_end: int, name: str):
    import bpy

    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj is target_rig)
    bpy.context.view_layer.objects.active = target_rig
    bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.nla.bake(
        frame_start=frame_start, frame_end=frame_end, step=1, only_selected=False, visual_keying=True,
        clear_constraints=True, clear_parents=False, use_current_action=False, bake_types={"POSE"},
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    action = target_rig.animation_data.action
    action.name = name
    return action


def _root_curves(action, bone: str = "root"):
    path = f'pose.bones["{bone}"].location'
    return {fc.array_index: fc for fc in action_fcurves(action) if fc.data_path == path}


def _normalise_root(action, scale: float, bone: str = "root") -> tuple[float, float, float]:
    """Scale root travel to the target's size and start it at the origin. Returns root motion."""
    curves = _root_curves(action, bone)
    motion = [0.0, 0.0, 0.0]
    for index, fc in curves.items():
        if not fc.keyframe_points:
            continue
        first = fc.keyframe_points[0].co[1]
        for kp in fc.keyframe_points:
            kp.co[1] = (kp.co[1] - first) * scale
            kp.handle_left[1] = (kp.handle_left[1] - first) * scale
            kp.handle_right[1] = (kp.handle_right[1] - first) * scale
        if index < 3:
            motion[index] = float(fc.keyframe_points[-1].co[1])
    return (round(motion[0], 4), round(motion[1], 4), round(motion[2], 4))


def foot_slide_cm(rig, feet: list[str], frame_start: int, frame_end: int, contact_m: float = 0.03) -> float:
    """Worst horizontal drift of a planted foot, in centimetres. A foot counts as planted
    while its head is within ``contact_m`` of its lowest point in the clip."""
    import bpy

    scene = bpy.context.scene
    worst = 0.0
    for foot in feet:
        if foot not in rig.pose.bones:
            continue
        positions = []
        for frame in range(frame_start, frame_end + 1):
            scene.frame_set(frame)
            positions.append((rig.matrix_world @ rig.pose.bones[foot].head).copy())
        floor = min(p.z for p in positions)
        slide = 0.0
        for a, b in zip(positions, positions[1:]):
            if a.z <= floor + contact_m and b.z <= floor + contact_m:
                slide += math.hypot(b.x - a.x, b.y - a.y)
            else:
                worst = max(worst, slide)
                slide = 0.0
        worst = max(worst, slide)
    scene.frame_set(frame_start)
    return round(worst * 100.0, 2)


def _looks_like_loop(rig, frame_start: int, frame_end: int, tolerance_deg: float = 12.0) -> bool:
    import bpy

    scene = bpy.context.scene
    scene.frame_set(frame_start)
    first = {pb.name: pb.matrix_basis.to_quaternion() for pb in rig.pose.bones}
    scene.frame_set(frame_end)
    worst = 0.0
    for pb in rig.pose.bones:
        if pb.name == "root":
            continue
        worst = max(worst, math.degrees(first[pb.name].rotation_difference(pb.matrix_basis.to_quaternion()).angle))
    scene.frame_set(frame_start)
    return worst <= tolerance_deg


# ---------------------------------------------------------------- the run
def retarget(
    library: Path,
    source: Path,
    base_id: str,
    bone_map: dict[str, Any],
    name: str,
    loop: str = "auto",
    licence: str | None = None,
    tags: list[str] | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    import bpy

    base_file = library / "bases" / f"{base_id}.blend"
    if not base_file.exists():
        raise FileNotFoundError(f"base {base_id} not found at {base_file}")
    open_blend(base_file)
    scene = bpy.context.scene
    target_rig = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    rig_type = scene_prop("studio_rig_type", bone_map["target_rig_type"])
    if rig_type != bone_map["target_rig_type"]:
        raise ValueError(f"{base_id} is {rig_type}; the bone map targets {bone_map['target_rig_type']}")
    target_height = max((max((target_rig.matrix_world @ b.tail_local).z for b in target_rig.data.bones), 1e-3))

    src_rig, src_action = _import_source(source)
    frame_start, frame_end = (int(round(v)) for v in src_action.frame_range)
    frame_end = max(frame_end, frame_start + 1)
    src_height = max((max((src_rig.matrix_world @ b.tail_local).z for b in src_rig.data.bones), 1e-3))
    mapped = _constrain(target_rig, src_rig, bone_map)
    if not mapped:
        raise ValueError("no bone of the map exists on both rigs")

    action = _bake(target_rig, frame_start, frame_end, name)
    bpy.data.objects.remove(src_rig)
    for a in list(bpy.data.actions):
        if a is not action:
            bpy.data.actions.remove(a)
    root_motion = _normalise_root(action, target_height / src_height)
    frames = frame_end - frame_start + 1
    slide = foot_slide_cm(target_rig, bone_map.get("feet", []), frame_start, frame_end)
    is_loop = _looks_like_loop(target_rig, frame_start, frame_end) if loop == "auto" else loop == "yes"

    all_tags = merge_tags(tags or [], tags_from_name(name), tags_from_name(source.stem), tags_from_motion(root_motion, frames, scene.render.fps, is_loop))
    scene.frame_start, scene.frame_end = frame_start, frame_end
    for obj in [o for o in bpy.data.objects if o.type == "MESH"]:
        bpy.data.objects.remove(obj)  # a clip file holds the rig and the Action only
    for key in list(scene.keys()):
        if key.startswith("studio_"):
            del scene[key]
    scene["studio_kind"] = "clip"
    scene["studio_rig_type"] = rig_type
    scene["studio_loop"] = 1 if is_loop else 0
    scene["studio_tags"] = ",".join(all_tags)
    scene["studio_source"] = f"{source.parent.name}/{source.name}"
    scene["studio_licence"] = licence or "unknown"
    scene["studio_retarget_base"] = base_id
    scene["studio_foot_slide_cm"] = slide

    out = (out_dir or library / "clips" / "baked" / rig_type) / f"{name}.blend"
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out), compress=True)
    return {
        "clip_id": name, "rig_type": rig_type, "base_id": base_id, "source": str(source), "frames": frames,
        "loop": is_loop, "root_motion_m": root_motion, "foot_slide_cm": slide, "tags": all_tags,
        "mapped_bones": len(mapped), "file": out.relative_to(library).as_posix() if out.is_relative_to(library) else str(out),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="library")
    parser.add_argument("--source", required=True, help=".fbx or .blend with one animated armature")
    parser.add_argument("--base", required=True, help="base id, e.g. base_human_child")
    parser.add_argument("--map", required=True, help="bone map JSON")
    parser.add_argument("--name", required=True, help="clip id, clip_<slug>")
    parser.add_argument("--loop", default="auto", choices=["auto", "yes", "no"])
    parser.add_argument("--licence", default=None)
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--max-slide-cm", type=float, default=2.0, help="fail when a planted foot slides more than this")
    parser.add_argument("--rescan", action="store_true")
    args = parser.parse_args(script_args(argv))
    library = Path(args.library).resolve()
    record = retarget(library, Path(args.source), args.base, load_bone_map(Path(args.map)), args.name, args.loop, args.licence, args.tag)
    print(json.dumps(record, indent=2))
    if args.rescan:
        from tools.scan_library import main as scan_main

        scan_main(["--library", str(library)])
    return 0 if record["foot_slide_cm"] <= args.max_slide_cm else 2


if __name__ == "__main__":
    sys.exit(main())
