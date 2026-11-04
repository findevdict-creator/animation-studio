"""Build a tiny seed library programmatically so the scanner, lint and stage 1 have real
assets to work with before any artist touches Blender.

    blender -b --python tools/make_seed_library.py -- --library library
    python -m tools.make_seed_library --library library

Creates: one rigged human block figure (lib_char_girl_a), one rigged quadruped
(lib_char_fox_a), a lantern prop, a forest clearing environment with anchors, four
clips, three parametric bases (base_human_child, base_human_adult, base_quadruped),
eight garments, two hair pieces, two environment kits and four generators. These are placeholders with correct structure,
not art: the point is that every tool runs end to end against them.
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


FACE_KEYS = ["smile", "frown", "brow_up", "mouth_open", "blink_l", "blink_r", "vis_aa", "vis_ee", "vis_oh", "vis_mm"]
PROPORTION_SLIDERS = ["head_scale", "limb_length", "torso_width", "belly", "age"]
OUTFIT_SLOTS = "top,bottom,shoes,coat,hat,accessory"


def _material(name: str, rgba=(0.8, 0.8, 0.8, 1.0)):
    import bpy

    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = rgba
    return mat


def _assign(obj, mat) -> None:
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def make_human(library: Path, height: float, file: Path, as_base: bool = False, tags: str | None = None):
    import bpy

    scene = _reset()
    h = height
    body = _box("body", (0.25 * h, 0.12 * h, 0.4 * h), (0, 0, 0.55 * h))
    head = _box("head", (0.16 * h, 0.16 * h, 0.16 * h), (0, 0, 0.9 * h))
    leg_l = _box("leg_l", (0.08 * h, 0.1 * h, 0.35 * h), (-0.07 * h, 0, 0.175 * h))
    leg_r = _box("leg_r", (0.08 * h, 0.1 * h, 0.35 * h), (0.07 * h, 0, 0.175 * h))
    eyes = _box("eyes", (0.1 * h, 0.02 * h, 0.02 * h), (0, -0.08 * h, 0.92 * h))
    hair = _box("hair_cap", (0.17 * h, 0.17 * h, 0.04 * h), (0, 0, 0.99 * h))
    skin = _material("skin", (0.78, 0.55, 0.42, 1.0))
    for obj in (body, head, leg_l, leg_r):
        _assign(obj, skin)
    _assign(eyes, _material("eyes", (0.2, 0.12, 0.08, 1.0)))
    _assign(hair, _material("hair", (0.15, 0.1, 0.08, 1.0)))
    # Face shape keys on the head; proportion sliders are shape keys too (neutral at 0, -1..1).
    _add_shape_keys(head, FACE_KEYS)
    if as_base:
        for name in PROPORTION_SLIDERS:
            for obj in (head, body):
                if not obj.data.shape_keys:
                    obj.shape_key_add(name="Basis", from_mix=False)
                kb = obj.shape_key_add(name=name, from_mix=False)
                kb.slider_min = -1.0
                for v in kb.data:
                    v.co.z += 0.02 * h  # a token deformation
    rig = _rig(
        [
            ("root", (0, 0, 0), (0, 0.2, 0), None),
            ("spine", (0, 0, 0.35 * h), (0, 0, 0.75 * h), "root"),
            ("head", (0, 0, 0.75 * h), (0, 0, 0.98 * h), "spine"),
            ("upper_arm.R", (0.14 * h, 0, 0.72 * h), (0.2 * h, 0, 0.55 * h), "spine"),
            ("hand.R", (0.2 * h, 0, 0.55 * h), (0.25 * h, 0, 0.45 * h), "upper_arm.R"),
            ("upper_arm.L", (-0.14 * h, 0, 0.72 * h), (-0.2 * h, 0, 0.55 * h), "spine"),
            ("hand.L", (-0.2 * h, 0, 0.55 * h), (-0.25 * h, 0, 0.45 * h), "upper_arm.L"),
            ("thigh.R", (0.07 * h, 0, 0.35 * h), (0.07 * h, 0, 0.18 * h), "root"),
            ("foot.R", (0.07 * h, 0, 0.18 * h), (0.07 * h, 0.1 * h, 0.0), "thigh.R"),
            ("thigh.L", (-0.07 * h, 0, 0.35 * h), (-0.07 * h, 0, 0.18 * h), "root"),
            ("foot.L", (-0.07 * h, 0, 0.18 * h), (-0.07 * h, 0.1 * h, 0.0), "thigh.L"),
        ]
    )
    for obj in (body, head, leg_l, leg_r, eyes, hair):
        obj.parent = rig
    _props(
        scene,
        studio_kind="base" if as_base else "character",
        studio_rig_type="rigify_human",
        studio_outfit_slots=OUTFIT_SLOTS,
        studio_tags=tags or ("human,child" if h < 1.5 else "human,adult"),
    )
    if as_base:
        _props(scene, studio_proportion_sliders=",".join(PROPORTION_SLIDERS), studio_material_slots="skin,hair,eyes")
    _save(file)


def make_quadruped_base(library: Path):
    """base_quadruped: the fox's shape with sliders, so animal characters are parametric too."""
    scene = _reset()
    body = _box("body", (0.45, 0.2, 0.2), (0, 0, 0.3))
    head = _box("head", (0.18, 0.15, 0.15), (0.3, 0, 0.42))
    for name, x, y in (("leg_fl", 0.15, 0.06), ("leg_fr", 0.15, -0.06), ("leg_bl", -0.15, 0.06), ("leg_br", -0.15, -0.06)):
        _assign(_box(name, (0.06, 0.06, 0.2), (x, y, 0.1)), _material("fur", (0.8, 0.45, 0.2, 1.0)))
    tail = _box("tail", (0.3, 0.08, 0.08), (-0.35, 0, 0.35))
    eyes = _box("eyes", (0.02, 0.08, 0.02), (0.39, 0, 0.46))
    fur = _material("fur", (0.8, 0.45, 0.2, 1.0))
    for obj in (body, head, tail):
        _assign(obj, fur)
    _assign(eyes, _material("eyes", (0.1, 0.1, 0.1, 1.0)))
    _add_shape_keys(head, ["smile", "ears_back", "mouth_open", "blink_l", "blink_r"])
    for name in ("head_scale", "limb_length", "torso_width", "belly", "age"):
        kb = body.shape_key_add(name=name, from_mix=False) if body.data.shape_keys else (body.shape_key_add(name="Basis", from_mix=False), body.shape_key_add(name=name, from_mix=False))[1]
        kb.slider_min = -1.0
        for v in kb.data:
            v.co.z += 0.01
    rig = _rig(
        [
            ("root", (0, 0, 0), (0.2, 0, 0), None),
            ("spine", (-0.2, 0, 0.3), (0.2, 0, 0.3), "root"),
            ("head", (0.2, 0, 0.35), (0.4, 0, 0.45), "spine"),
            ("tail", (-0.2, 0, 0.3), (-0.5, 0, 0.4), "spine"),
        ]
    )
    for obj in (body, head, tail, eyes):
        obj.parent = rig
    _props(scene, studio_kind="base", studio_rig_type="rigify_quadruped", studio_outfit_slots="collar,accessory",
           studio_proportion_sliders="head_scale,limb_length,torso_width,belly,age", studio_material_slots="fur,eyes",
           studio_tags="quadruped,animal")
    _save(library / "bases" / "base_quadruped.blend")


def make_garment(library: Path, name: str, slot: str, size, location, fits: str, tags: str):
    """Garments are authored for a 1.0 m tall base and scaled to the target base's height."""
    scene = _reset()
    obj = _box(name, size, location)
    _assign(obj, _material(slot, (0.6, 0.6, 0.6, 1.0)))
    _props(scene, studio_kind="garment", studio_slot=slot, studio_fits=fits, studio_tags=tags, studio_ref_height_m=1.0)
    _save(library / "garments" / f"{name}.blend")


def make_hair(library: Path, name: str, fits: str):
    scene = _reset()
    obj = _box(name, (0.18, 0.18, 0.06), (0, 0, 1.0))
    _assign(obj, _material("hair", (0.15, 0.1, 0.08, 1.0)))
    for side in (-1, 1):
        _assign(_box(f"{name}_braid_{'l' if side < 0 else 'r'}", (0.04, 0.04, 0.25), (side * 0.1, 0.02, 0.8)), bpy_mat("hair"))
    _props(scene, studio_kind="hair", studio_fits=fits, studio_ref_height_m=1.0, studio_tags="hair")
    _save(library / "hair" / f"{name}.blend")


def bpy_mat(name: str):
    import bpy

    return bpy.data.materials[name]


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


def make_kit_piece(library: Path, kit: str, name: str, size, snap: str, tags: str, color=(0.5, 0.4, 0.3, 1.0)):
    """A kit piece sits on the ground at the origin; compose_env places copies of it."""
    scene = _reset()
    obj = _box(name, size, (0, 0, size[2] / 2))
    _assign(obj, _material(f"{kit}_{name.split('_')[-2] if name.count('_') > 2 else 'mat'}", color))
    _props(scene, studio_kind="kit_piece", studio_kit=kit, studio_snap_points=snap, studio_tags=tags)
    _save(library / "kits" / kit.removeprefix("kit_") / f"{name}.blend")


def make_generator(library: Path, name: str, kind: str, params: str, mesh_size=None, color=(0.3, 0.6, 0.2, 1.0)):
    """A generator file: its parameters as scene properties and, for scatter generators, the
    mesh it scatters when no kit pieces are named (a grass tuft, a pebble)."""
    scene = _reset()
    if mesh_size is not None:
        _assign(_box(name, mesh_size, (0, 0, mesh_size[2] / 2)), _material(name, color))
    _props(scene, studio_kind="generator", studio_generator_kind=kind, studio_params=params, studio_tags=kind)
    _save(library / "generators" / f"{name}.blend")


def make_kits(library: Path) -> None:
    make_kit_piece(library, "kit_forest", "kit_forest_stump_a", (1.2, 1.2, 0.5), "top", "seat,wood")
    make_kit_piece(library, "kit_forest", "kit_forest_birch_tall", (0.5, 0.5, 9.0), "", "tree,birch", (0.85, 0.85, 0.8, 1.0))
    make_kit_piece(library, "kit_forest", "kit_forest_birch_short", (0.4, 0.4, 5.0), "", "tree,birch", (0.85, 0.85, 0.8, 1.0))
    make_kit_piece(library, "kit_forest", "kit_forest_rock_a", (1.5, 1.0, 0.8), "top", "rock", (0.45, 0.45, 0.5, 1.0))
    make_kit_piece(library, "kit_forest", "kit_forest_log_a", (3.0, 0.6, 0.6), "top", "seat,wood,log")
    make_kit_piece(library, "kit_interior", "kit_interior_floor_4x4", (4.0, 4.0, 0.1), "n,s,e,w", "floor", (0.6, 0.45, 0.3, 1.0))
    make_kit_piece(library, "kit_interior", "kit_interior_wall_4m", (4.0, 0.2, 2.8), "left,right", "wall", (0.9, 0.88, 0.8, 1.0))
    make_kit_piece(library, "kit_interior", "kit_interior_table_a", (1.4, 0.8, 0.75), "top", "table,furniture")
    make_kit_piece(library, "kit_interior", "kit_interior_chair_a", (0.45, 0.45, 0.9), "seat", "seat,chair,furniture")
    make_generator(library, "gen_terrain_rolling", "terrain", "height_m:0:10,roughness:0:1")
    make_generator(library, "gen_scatter_grass", "scatter", "density:0:1,exclude_radius_m:0:10", (0.3, 0.3, 0.25))
    make_generator(library, "gen_scatter_trees", "scatter", "density:0:1,exclude_radius_m:0:10")
    make_generator(library, "gen_scatter_pebbles", "scatter", "density:0:1", (0.15, 0.12, 0.08), (0.5, 0.5, 0.5, 1.0))


MIXAMO_BONES = [
    ("mixamorig:Hips", (0, 0, 0.95), (0, 0.1, 0.95), None),  # points +Y so its local Y travel is forward
    ("mixamorig:Spine", (0, 0, 0.95), (0, 0, 1.45), "mixamorig:Hips"),
    ("mixamorig:Head", (0, 0, 1.45), (0, 0, 1.75), "mixamorig:Spine"),
    ("mixamorig:RightArm", (0.2, 0, 1.4), (0.45, 0, 1.4), "mixamorig:Spine"),
    ("mixamorig:RightHand", (0.45, 0, 1.4), (0.7, 0, 1.4), "mixamorig:RightArm"),
    ("mixamorig:LeftArm", (-0.2, 0, 1.4), (-0.45, 0, 1.4), "mixamorig:Spine"),
    ("mixamorig:LeftHand", (-0.45, 0, 1.4), (-0.7, 0, 1.4), "mixamorig:LeftArm"),
    ("mixamorig:RightUpLeg", (0.1, 0, 0.95), (0.1, 0, 0.5), "mixamorig:Hips"),
    ("mixamorig:RightFoot", (0.1, 0, 0.5), (0.1, 0.15, 0.0), "mixamorig:RightUpLeg"),
    ("mixamorig:LeftUpLeg", (-0.1, 0, 0.95), (-0.1, 0, 0.5), "mixamorig:Hips"),
    ("mixamorig:LeftFoot", (-0.1, 0, 0.5), (-0.1, 0.15, 0.0), "mixamorig:LeftUpLeg"),
]


def make_mocap_source(library: Path, name: str = "seed_walk_forward", frames: int = 40, forward_m: float = 1.6):
    """A stand-in for a Mixamo export: a 1.75 m armature with Mixamo bone names and a walk
    cycle (hips travel forward, legs swing, arms counter-swing). Lives under clips/source,
    which the scanner ignores; tools/retarget.py bakes it onto a base rig."""
    import bpy

    scene = _reset()
    rig = _rig(MIXAMO_BONES)
    rig.name = rig.data.name = "Armature"
    action = bpy.data.actions.new(name)
    rig.animation_data_create()
    rig.animation_data.action = action
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="POSE")
    pose = rig.pose.bones
    for bone in pose:
        bone.rotation_mode = "XYZ"
    for frame in range(1, frames + 1):
        t = (frame - 1) / (frames - 1)
        phase = 2 * math.pi * t
        pose["mixamorig:Hips"].location = (0, forward_m * t, 0.02 * math.sin(2 * phase))
        pose["mixamorig:Hips"].keyframe_insert("location", frame=frame)
        swing = math.radians(28) * math.sin(phase)
        pose["mixamorig:RightUpLeg"].rotation_euler = (swing, 0, 0)
        pose["mixamorig:LeftUpLeg"].rotation_euler = (-swing, 0, 0)
        pose["mixamorig:RightArm"].rotation_euler = (-swing * 0.6, 0, 0)
        pose["mixamorig:LeftArm"].rotation_euler = (swing * 0.6, 0, 0)
        pose["mixamorig:Spine"].rotation_euler = (math.radians(2) * math.sin(2 * phase), 0, math.radians(3) * math.sin(phase))
        for b in ("mixamorig:RightUpLeg", "mixamorig:LeftUpLeg", "mixamorig:RightArm", "mixamorig:LeftArm", "mixamorig:Spine"):
            pose[b].keyframe_insert("rotation_euler", frame=frame)
    bpy.ops.object.mode_set(mode="OBJECT")
    scene.frame_start, scene.frame_end = 1, frames
    _props(scene, studio_source_rig="mixamo", studio_licence="CC0")
    _save(library / "clips" / "source" / "mixamo" / f"{name}.blend")
    (library / "clips" / "source" / "LICENCES.md").write_text(
        "# Clip sources\n\n| folder | source | licence |\n| --- | --- | --- |\n| mixamo/seed_* | generated by tools/make_seed_library.py | CC0 |\n"
    )


HUMAN_FITS = "base_human_child,base_human_adult"


def build(library: Path) -> None:
    make_human(library, 1.2, library / "characters" / "lib_char_girl_a.blend")
    make_human(library, 1.2, library / "bases" / "base_human_child.blend", as_base=True)
    make_human(library, 1.75, library / "bases" / "base_human_adult.blend", as_base=True)
    make_quadruped_base(library)
    make_fox(library)
    make_lantern(library)
    make_forest(library)
    make_clip(library, "rigify_human", "clip_walk_cautious", 32, True, 1.1, "locomotion,slow,forward")
    make_clip(library, "rigify_human", "clip_raise_arm_r", 18, False, 0.0, "gesture")
    make_clip(library, "rigify_human", "clip_idle_breath", 48, True, 0.0, "idle")
    make_clip(library, "rigify_quadruped", "clip_fox_sit", 24, True, 0.0, "idle")
    # Wardrobe, authored at 1.0 m and scaled per base.
    make_garment(library, "garm_tee_a", "top", (0.27, 0.14, 0.2), (0, 0, 0.62), HUMAN_FITS, "casual")
    make_garment(library, "garm_shorts_a", "bottom", (0.26, 0.13, 0.12), (0, 0, 0.42), HUMAN_FITS, "casual")
    make_garment(library, "garm_trousers_a", "bottom", (0.26, 0.13, 0.36), (0, 0, 0.19), HUMAN_FITS, "casual")
    make_garment(library, "garm_boots_a", "shoes", (0.24, 0.16, 0.08), (0, 0.02, 0.04), HUMAN_FITS, "boots,rain")
    make_garment(library, "garm_raincoat_a", "coat", (0.3, 0.16, 0.4), (0, 0, 0.56), HUMAN_FITS, "outerwear,rain")
    make_garment(library, "garm_cap_a", "hat", (0.18, 0.18, 0.05), (0, 0, 1.02), HUMAN_FITS, "cap")
    make_garment(library, "garm_scarf_a", "accessory", (0.2, 0.2, 0.05), (0, 0, 0.74), HUMAN_FITS, "scarf,winter")
    make_garment(library, "garm_collar_a", "collar", (0.2, 0.2, 0.04), (0.18, 0, 0.42), "base_quadruped", "collar")
    make_hair(library, "hair_two_braids", HUMAN_FITS)
    make_hair(library, "hair_short_a", HUMAN_FITS)
    make_kits(library)
    make_mocap_source(library)
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
