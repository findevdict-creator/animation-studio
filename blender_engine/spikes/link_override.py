"""Spike (a), architecture 7.2 and risk R4: can a scene link a published character and an
outfit, retarget the outfit's Armature modifier to the character's overridden rig, add a
body Mask modifier, pose the rig, save with relative paths, and reopen it, while the source
files stay byte-for-byte unchanged?

    python blender_engine/spikes/link_override.py <workdir> build      # writes the sources and the scene
    python blender_engine/spikes/link_override.py <workdir> verify     # fresh process: reopens and checks

Prints one JSON object with every measurement; exit code 0 only if every check passed.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import bpy  # first: with the bpy wheel, bmesh and mathutils exist only after bpy loads
import bmesh
from mathutils import Quaternion, Vector

CHECKS: dict[str, object] = {}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reset() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def save(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), relative_remap=True, compress=False)


def armature(name: str, coll) -> bpy.types.Object:
    data = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, data)
    coll.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for bname, head, tail, parent in (("root", (0, 0, 0), (0, 0.2, 0), None),
                                      ("DEF-spine", (0, 0, 0.5), (0, 0, 1.0), "root"),
                                      ("DEF-upper_arm.L", (0.2, 0, 0.9), (0.6, 0, 0.9), "DEF-spine")):
        b = data.edit_bones.new(bname)
        b.head, b.tail = head, tail
        if parent:
            b.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return rig


def box_mesh(name: str, coll, size, center, groups: dict[str, callable]) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=4, use_grid_fill=True)
    for v in bm.verts:
        v.co = Vector((v.co.x * size[0] + center[0], v.co.y * size[1] + center[1], v.co.z * size[2] + center[2]))
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    coll.objects.link(obj)
    for gname, pick in groups.items():
        g = obj.vertex_groups.new(name=gname)
        idx = [v.index for v in mesh.vertices if pick(v.co)]
        if idx:
            g.add(idx, 1.0, "REPLACE")
    return obj


def build(work: Path) -> None:
    # --- character.blend: collection CHAR with rig + body (vertex groups incl. a mask region)
    reset()
    char = bpy.data.collections.new("CHAR")
    bpy.context.scene.collection.children.link(char)
    rig = armature("rig", char)
    body = box_mesh("body", char, (0.3, 0.2, 1.0), (0, 0, 0.5), {
        "DEF-spine": lambda c: c.x < 0.15,
        "DEF-upper_arm.L": lambda c: c.x >= 0.15,
        "mask_torso": lambda c: abs(c.x) < 0.1 and 0.4 < c.z < 0.9,
    })
    body.parent = rig
    body.modifiers.new("rig", "ARMATURE").object = rig
    char_file = work / "assets/character/CHAR-001/1.0/character.blend"
    save(char_file)

    # --- outfit.blend: collection OUTFIT, garment bound to a placeholder rig object
    reset()
    outfit = bpy.data.collections.new("OUTFIT")
    bpy.context.scene.collection.children.link(outfit)
    target = armature("RIG_TARGET", outfit)
    shirt = box_mesh("shirt", outfit, (0.34, 0.24, 0.5), (0, 0, 0.7), {
        "DEF-spine": lambda c: c.x < 0.15,
        "DEF-upper_arm.L": lambda c: c.x >= 0.15,
    })
    shirt.modifiers.new("rig", "ARMATURE").object = target
    shirt["studio_hides"] = "torso"
    outfit_file = work / "assets/outfit/OUTFIT-002/1.0/outfit.blend"
    save(outfit_file)

    before = {"character": sha(char_file), "outfit": sha(outfit_file)}

    # --- scene.blend: link both, override, retarget, mask, pose
    reset()
    scene = bpy.context.scene
    scene_file = work / "scene/scene.blend"
    scene_file.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(scene_file))  # so relative paths have a base

    def link_and_override(path: Path, name: str):
        """Link a collection, then make a library-override hierarchy of it (what the UI's
        "Make Library Override - Fully Editable" does): the overrides are local and hold
        the scene's state; mesh data stays in the library file."""
        with bpy.data.libraries.load(str(path), link=True, relative=True) as (src, dst):
            dst.collections = [name]
        linked = dst.collections[0]
        inst = bpy.data.objects.new(f"{name}_instance", None)
        inst.instance_type = "COLLECTION"
        inst.instance_collection = linked
        scene.collection.objects.link(inst)
        root = linked.override_hierarchy_create(scene, bpy.context.view_layer, reference=inst, do_fully_editable=True)
        bpy.data.objects.remove(inst)
        return root

    char_root = link_and_override(char_file, "CHAR")
    outfit_root = link_and_override(outfit_file, "OUTFIT")
    CHECKS["override_created"] = bool(char_root and char_root.override_library and outfit_root and outfit_root.override_library)

    o_rig = next(o for o in bpy.data.objects if o.name.startswith("rig") and o.override_library)
    o_body = next(o for o in bpy.data.objects if o.name.startswith("body") and o.override_library)
    o_shirt = next(o for o in bpy.data.objects if o.name.startswith("shirt") and o.override_library)

    o_shirt.modifiers["rig"].object = o_rig
    o_shirt.parent = o_rig
    mask = o_body.modifiers.new("mask_torso", "MASK")
    mask.vertex_group = "mask_torso"
    mask.invert_vertex_group = True
    # hide the outfit's placeholder rig in this scene
    for o in bpy.data.objects:
        if o.name.startswith("RIG_TARGET") and o.override_library:
            o.hide_render = True
            o.hide_viewport = True

    # Static pose in the bone's own rotation mode (quaternion, as published). Changing
    # rotation_mode on an override is a trap: on reload the mode override can be applied
    # after the rotation value and converts it away (see the spike report).
    pb = o_rig.pose.bones["DEF-upper_arm.L"]
    pb.rotation_quaternion = Quaternion((0, 0, 1), math.radians(45))
    # Animation the production way: a scene-local Action assigned to the overridden rig.
    action = bpy.data.actions.new("ali_1_wave")
    o_rig.animation_data_create()
    o_rig.animation_data.action = action
    fc_path = 'pose.bones["DEF-spine"].rotation_quaternion'
    if hasattr(action, "fcurve_ensure_for_datablock"):
        curves = [action.fcurve_ensure_for_datablock(o_rig, fc_path, index=i) for i in range(4)]
    else:  # pragma: no cover - pre-5.0 API
        curves = [action.fcurves.new(fc_path, index=i) for i in range(4)]
    q10 = Quaternion((1, 0, 0), math.radians(10))
    for i, c in enumerate(curves):
        c.keyframe_points.insert(1, (1, 0, 0, 0)[i])
        c.keyframe_points.insert(25, q10[i])
    # A new custom property on an override is dropped on save unless marked overridable.
    o_rig["studio_instance_key"] = "ali_1"
    o_rig.property_overridable_library_set('["studio_instance_key"]', True)
    bpy.context.view_layer.update()
    CHECKS["override_ops_before_update"] = len(o_rig.override_library.properties)
    if "--no-operations-update" not in sys.argv:
        # Overrides store their differences from the reference as "operations". Saving
        # computes them anyway (the --no-operations-update run shows the same result); the
        # engine calls it explicitly so the operations can be inspected before the save.
        for id_ in (*bpy.data.objects, *bpy.data.collections, *bpy.data.armatures, *bpy.data.meshes):
            if id_.override_library and not id_.library:
                id_.override_library.operations_update()
    CHECKS["override_ops_after_update"] = len(o_rig.override_library.properties)
    save(scene_file)

    after = {"character": sha(char_file), "outfit": sha(outfit_file)}
    CHECKS["sources_unchanged_after_scene_save"] = before == after
    (work / "hashes.json").write_text(json.dumps(before))


def verify(work: Path) -> None:
    scene_file = work / "scene/scene.blend"
    bpy.ops.wm.open_mainfile(filepath=str(scene_file), load_ui=False, use_scripts=False)
    libs = {lib.name: lib.filepath for lib in bpy.data.libraries}
    CHECKS["library_paths"] = libs
    CHECKS["library_paths_relative"] = all(p.startswith("//") for p in libs.values()) and len(libs) == 2
    CHECKS["no_absolute_path_in_file"] = str(work.resolve()).encode() not in scene_file.read_bytes()

    o_rig = next(o for o in bpy.data.objects if o.name.startswith("rig") and o.override_library)
    o_body = next(o for o in bpy.data.objects if o.name.startswith("body") and o.override_library)
    o_shirt = next(o for o in bpy.data.objects if o.name.startswith("shirt") and o.override_library)
    CHECKS["outfit_bound_to_character_rig"] = o_shirt.modifiers["rig"].object == o_rig
    CHECKS["mask_modifier_kept"] = "mask_torso" in o_body.modifiers and o_body.modifiers["mask_torso"].invert_vertex_group
    q = o_rig.pose.bones["DEF-upper_arm.L"].rotation_quaternion
    angle = math.degrees(q.angle) * (1 if q.axis.z > 0 else -1)
    CHECKS["pose_kept_deg"] = round(angle, 3)
    CHECKS["pose_kept"] = abs(angle - 45) < 1e-3
    ad = o_rig.animation_data
    CHECKS["action_kept"] = bool(ad and ad.action and ad.action.name == "ali_1_wave")
    bpy.context.scene.frame_set(25)
    spine = o_rig.pose.bones["DEF-spine"].rotation_quaternion
    CHECKS["action_evaluates_deg"] = round(math.degrees(spine.angle), 3)
    CHECKS["action_evaluates"] = abs(math.degrees(spine.angle) - 10) < 1e-2
    bpy.context.scene.frame_set(1)
    # Finding, not a pass/fail check: report what Blender does.
    CHECKS["finding_new_custom_property_on_override"] = (
        "kept" if o_rig.get("studio_instance_key") == "ali_1" else "dropped on save")
    CHECKS["mesh_data_still_linked"] = o_body.data.library is not None and o_shirt.data.library is not None

    dg = bpy.context.evaluated_depsgraph_get()
    rest = [v.co.copy() for v in o_shirt.data.vertices]
    ev = o_shirt.evaluated_get(dg).to_mesh()
    moved = sum(1 for a, b in zip(rest, ev.vertices) if (a - b.co).length > 1e-4)
    CHECKS["garment_deforms_with_character_rig_vertices"] = moved
    CHECKS["garment_deforms"] = moved > 0
    body_ev = o_body.evaluated_get(dg).to_mesh()
    CHECKS["masked_body_vertices"] = len(o_body.data.vertices) - len(body_ev.vertices)
    CHECKS["mask_hides_torso"] = len(body_ev.vertices) < len(o_body.data.vertices)

    hashes = json.loads((work / "hashes.json").read_text())
    now = {"character": sha(work / "assets/character/CHAR-001/1.0/character.blend"),
           "outfit": sha(work / "assets/outfit/OUTFIT-002/1.0/outfit.blend")}
    CHECKS["sources_unchanged_after_reopen"] = hashes == now
    CHECKS["blender_version"] = bpy.app.version_string


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    work, step = Path(args[-2]).resolve(), args[-1]
    (build if step == "build" else verify)(work)
    print("SPIKE_RESULT " + json.dumps(CHECKS))
    failed = [k for k, v in CHECKS.items() if v is False]
    sys.exit(1 if failed else 0)
