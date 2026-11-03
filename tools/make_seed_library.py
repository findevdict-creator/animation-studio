"""Build a tiny seed library programmatically so the scanner, lint and stage 1 have real
assets to work with before any artist touches Blender.

    blender -b --python tools/make_seed_library.py -- --library library
    python -m tools.make_seed_library --library library

Creates: one rigged human block figure (lib_char_girl_a), one rigged quadruped
(lib_char_fox_a), a lantern prop, a forest clearing environment with anchors, three
clips, and a parametric base (base_human_child). These are placeholders with correct
structure, not art.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from tools.blend_common import script_args


def _reset():
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.render.fps = 24
    return scene


def _save(path: Path):
    import bpy

    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True)


def _box(name, size, location, collection=None):
    import bpy

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(scale=True)
    if collection is not None:
        for c in obj.users_collection:
            c.objects.unlink(obj)
        collection.objects.link(obj)
    return obj


def _add_shape_keys(obj, names):
    obj.shape_key_add(name="Basis", from_mix=False)
    for n in names:
        kb = obj.shape_key_add(name=n, from_mix=False)
        kb.data[0].co.z += 0.01  # a token deformation


def _rig(bones: list[tuple[str, tuple, tuple, str | None]]):
    """bones: (name, head, tail, parent)"""
    import bpy

    arm = bpy.data.armatures.new("rig")
    rig = bpy.data.objects.new("rig", arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    created = {}
    for name, head, tail, parent in bones:
        b = arm.edit_bones.new(name)
        b.head, b.tail = head, tail
        if parent:
            b.parent = created[parent]
        created[name] = b
    bpy.ops.object.mode_set(mode="OBJECT")
    return rig


def _props(scene, **props):
    for k, v in props.items():
        scene[k] = v


def make_human(library: Path, height: float, file: Path, as_base: bool = False):
    import bpy

    scene = _reset()
    h = height
    body = _box("body", (0.25 * h, 0.12 * h, 0.4 * h), (0, 0, 0.55 * h))
    head = _box("head", (0.16 * h, 0.16 * h, 0.16 * h), (0, 0, 0.9 * h))
    _box("leg_l", (0.08 * h, 0.1 * h, 0.35 * h), (-0.07 * h, 0, 0.175 * h))
    _box("leg_r", (0.08 * h, 0.1 * h, 0.35 * h), (0.07 * h, 0, 0.175 * h))
    _add_shape_keys(head, ["smile", "frown", "brow_up", "mouth_open", "blink_l", "blink_r"] + (["head_scale", "limb_length"] if as_base else []))
    rig = _rig(
        [
            ("root", (0, 0, 0), (0, 0.2, 0), None),
            ("spine", (0, 0, 0.35 * h), (0, 0, 0.75 * h), "root"),
            ("head", (0, 0, 0.75 * h), (0, 0, 0.98 * h), "spine"),
            ("hand.R", (0.2 * h, 0, 0.5 * h), (0.25 * h, 0, 0.45 * h), "spine"),
            ("hand.L", (-0.2 * h, 0, 0.5 * h), (-0.25 * h, 0, 0.45 * h), "spine"),
        ]
    )
    for obj in (body, head):
        obj.parent = rig
    _props(
        scene,
        studio_kind="base" if as_base else "character",
        studio_rig_type="rigify_human",
        studio_outfit_slots="top,bottom,shoes,coat,hat,accessory",
        studio_tags="human,child" if h < 1.5 else "human,adult",
    )
    if as_base:
        _props(scene, studio_proportion_sliders="head_scale,limb_length", studio_material_slots="skin,hair,eyes")
    _save(file)


def make_fox(library: Path):
    scene = _reset()
    body = _box("body", (0.45, 0.2, 0.2), (0, 0, 0.3))
    head = _box("head", (0.18, 0.15, 0.15), (0.3, 0, 0.42))
    for name, x, y in (("leg_fl", 0.15, 0.06), ("leg_fr", 0.15, -0.06), ("leg_bl", -0.15, 0.06), ("leg_br", -0.15, -0.06)):
        _box(name, (0.06, 0.06, 0.2), (x, y, 0.1))
    _add_shape_keys(head, ["smile", "ears_back", "mouth_open"])
    rig = _rig(
        [
            ("root", (0, 0, 0), (0.2, 0, 0), None),
            ("spine", (-0.2, 0, 0.3), (0.2, 0, 0.3), "root"),
            ("head", (0.2, 0, 0.35), (0.4, 0, 0.45), "spine"),
        ]
    )
    body.parent = head.parent = rig
    _props(scene, studio_kind="character", studio_rig_type="rigify_quadruped", studio_tags="fox,quadruped,animal")
    _save(library / "characters" / "lib_char_fox_a.blend")


def make_lantern(library: Path):
    scene = _reset()
    _box("lantern", (0.18, 0.18, 0.3), (0, 0, 0.15))
    _box("handle", (0.02, 0.02, 0.08), (0, 0, 0.34))
    _props(scene, studio_kind="prop", studio_attach_bone="hand.R", studio_tags="handheld,light,brass")
    _save(library / "props" / "lib_prop_lantern.blend")


def make_forest(library: Path):
    import bpy

    scene = _reset()
    _box("ground", (60, 60, 0.1), (0, 0, -0.05))
    _box("stump", (1.2, 1.2, 0.5), (0, 0, 0.25))
    for i in range(12):
        a = 2 * math.pi * i / 12
        r = 12 + (i % 3) * 4
        _box(f"tree_{i:02d}", (0.5, 0.5, 8 + (i % 4)), (r * math.cos(a), r * math.sin(a), 4 + (i % 4) / 2))
    for name, loc in (("center", (0, 0, 0)), ("stump", (0, 0.9, 0)), ("path_entry", (-8, -12, 0)), ("stream_bank", (10, 2, 0))):
        empty = bpy.data.objects.new(f"anchor_{name}", None)
        empty.location = loc
        scene.collection.objects.link(empty)
    for variant in ("day", "dusk", "night"):
        coll = bpy.data.collections.new(f"time_{variant}")
        scene.collection.children.link(coll)
        light_data = bpy.data.lights.new(f"sun_{variant}", "SUN")
        light_data.energy = {"day": 4.0, "dusk": 1.5, "night": 0.2}[variant]
        light = bpy.data.objects.new(f"sun_{variant}", light_data)
        light.rotation_euler = (math.radians(50), 0, math.radians(30))
        coll.objects.link(light)
    _props(scene, studio_kind="environment", studio_time_variants="day,dusk,night", studio_tags="forest,exterior")
    _save(library / "envs" / "lib_env_forest_a.blend")


def make_clip(library: Path, rig_type: str, name: str, frames: int, loop: bool, forward_m: float, tags: str):
    import bpy

    scene = _reset()
    rig = _rig([("root", (0, 0, 0), (0, 0.2, 0), None), ("spine", (0, 0, 0.3), (0, 0, 0.8), "root")])
    action = bpy.data.actions.new(name)
    rig.animation_data_create()
    rig.animation_data.action = action
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="POSE")
    root = rig.pose.bones["root"]
    root.location = (0, 0, 0)
    root.keyframe_insert("location", frame=1)
    root.location = (0, forward_m, 0)
    root.keyframe_insert("location", frame=frames)
    spine = rig.pose.bones["spine"]
    spine.rotation_euler = (0, 0, 0)
    spine.keyframe_insert("rotation_euler", frame=1)
    spine.rotation_euler = (0.1, 0, 0)
    spine.keyframe_insert("rotation_euler", frame=frames)
    bpy.ops.object.mode_set(mode="OBJECT")
    scene.frame_start, scene.frame_end = 1, frames
    _props(scene, studio_kind="clip", studio_rig_type=rig_type, studio_loop=1 if loop else 0, studio_tags=tags,
           studio_source="seed/procedural", studio_licence="CC0")
    _save(library / "clips" / "baked" / rig_type / f"{name}.blend")


def build(library: Path) -> None:
    make_human(library, 1.2, library / "characters" / "lib_char_girl_a.blend")
    make_human(library, 1.2, library / "bases" / "base_human_child.blend", as_base=True)
    make_fox(library)
    make_lantern(library)
    make_forest(library)
    make_clip(library, "rigify_human", "clip_walk_cautious", 32, True, 1.1, "locomotion,slow,forward")
    make_clip(library, "rigify_human", "clip_raise_arm_r", 18, False, 0.0, "gesture")
    make_clip(library, "rigify_human", "clip_idle_breath", 48, True, 0.0, "idle")
    make_clip(library, "rigify_quadruped", "clip_fox_sit", 24, True, 0.0, "idle")
    (library / "lighting_presets.json").write_text('["day", "dusk", "night", "interior_warm"]\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="library")
    args = parser.parse_args(script_args(argv))
    build(Path(args.library).resolve())
    print(f"seed library written to {args.library}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
