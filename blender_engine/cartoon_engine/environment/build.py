"""The prop file and the environment file.

``prop.blend`` holds one ``PROP_<key>`` collection with the one prop object, stamped with the
specification digest. ``environment.blend`` holds two collections:

* ``ENV_<key>``: the floor (plus, outdoors, a wider ground to the horizon), walls with their
  openings cut out (rooms), the dressing (``DRESS_<id>`` objects, built by the same prop code
  the prop library uses), marks (``MARK_<name>`` empties) and camera marks (``CAM_<name>``
  cameras);
* ``LIGHTS_<key>``: the lighting rig of its preset (``lighting.build_rig``).

A room's south wall is the cutaway: it is built and casts shadows, but cameras see through
it (its camera ray visibility is off), as on a stage set. Rooms have no ceiling, so the sun
reaches the floor; the ceiling lights hang at the room's height.
"""

from __future__ import annotations

import math

from studio_contracts.environment import ResolvedEnvironment, ResolvedProp

from cartoon_engine.accessory.build import Shape, box
from cartoon_engine.character.materials import _principled
from cartoon_engine.environment import lighting
from cartoon_engine.environment.props import build_prop_object

WALL_T = 0.12


def _mesh_object(name: str, shape: Shape, mats: list):
    import bpy

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(shape.co, [], [list(f) for f in shape.faces])
    mesh.validate()
    for poly, m in zip(mesh.polygons, shape.mats, strict=False):
        poly.material_index = m
    for mat in mats:
        mesh.materials.append(mat)
    return bpy.data.objects.new(name, mesh)


def build_prop_file(rp: ResolvedProp, digest: str):
    import bpy

    obj = build_prop_object(rp, rp.object_name)
    coll = bpy.data.collections.new(f"PROP_{rp.key}")
    coll.objects.link(obj)
    for target in (coll, obj):
        target["studio_prop_name"] = rp.name
        target["studio_prop_digest"] = digest
        target["studio_item"] = rp.item
        target["studio_style_profile"] = rp.style_profile
        target["studio_category"] = rp.category
    bpy.context.scene.collection.children.link(coll)
    return coll, obj


def _wall_pieces(length: float, height: float, openings) -> list[tuple[float, float, float, float]]:
    """(start, end, bottom, top) pieces of one wall, measured along it from its -end."""
    cuts = sorted((o.offset_m - o.width_m / 2 + length / 2, o.offset_m + o.width_m / 2 + length / 2, o.sill_m,
                    o.head_m) for o in openings)
    pieces, x = [], 0.0
    for a, b, sill, head in cuts:
        if a > x:
            pieces.append((x, a, 0.0, height))
        if sill > 0:
            pieces.append((a, b, 0.0, sill))
        if head < height:
            pieces.append((a, b, head, height))
        x = b
    if x < length:
        pieces.append((x, length, 0.0, height))
    return pieces


def build_environment_file(env: ResolvedEnvironment, digest: str):
    """Build the set into the open file; returns (set collection, lights collection, info)."""
    import bpy

    w, d, h = env.size
    coll = bpy.data.collections.new(env.collection_name)
    bpy.context.scene.collection.children.link(coll)
    floor_mat = _principled(f"{env.key}_floor", env.floor_color, env.floor_roughness, 0.2)
    info: dict = {"walls": [], "dressing": [], "marks": [], "cameras": []}

    s = Shape()
    box(s, (0, 0, -0.025), (w, d, 0.05), 0, bevel=0.0)
    floor = _mesh_object("FLOOR", s, [floor_mat])
    floor["studio_role"] = "floor"
    coll.objects.link(floor)
    if env.kind == "outdoor":
        g = Shape()
        box(g, (0, 0, -0.035), (w * 6, d * 6, 0.05), 0, bevel=0.0)
        ground = _mesh_object("GROUND", g, [_principled(f"{env.key}_ground", env.floor_color,
                                                         env.floor_roughness, 0.1)])
        ground["studio_role"] = "ground"
        ground.data.materials[0].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = tuple(
            c * 0.82 for c in ground.data.materials[0].node_tree.nodes["Principled BSDF"].inputs[
                "Base Color"].default_value[:3]) + (1.0,)
        coll.objects.link(ground)
    else:
        wall_mat = _principled(f"{env.key}_wall", env.wall_color or "#E0E0E0", env.wall_roughness or 0.9, 0.1)
        trim_mat = _principled(f"{env.key}_skirting", "#F4F1EA", 0.5, 0.3)
        for wall in ("north", "south", "east", "west"):
            along_x = wall in ("north", "south")
            length = w if along_x else d
            ops = [o for o in env.openings if o.wall == wall]
            ws = Shape()
            for a, b, z0, z1 in _wall_pieces(length + 2 * WALL_T, h, ops):
                c = (a + b) / 2 - (length + 2 * WALL_T) / 2
                if along_x:
                    y = (d / 2 + WALL_T / 2) * (1 if wall == "north" else -1)
                    box(ws, (c, y, (z0 + z1) / 2), (b - a, WALL_T, z1 - z0), 0, bevel=0.0)
                else:
                    x = (w / 2 + WALL_T / 2) * (1 if wall == "east" else -1)
                    box(ws, (x, c, (z0 + z1) / 2), (WALL_T, b - a, z1 - z0), 0, bevel=0.0)
            if wall != "south":  # skirting board inside, in pieces between doors
                doors = [o for o in ops if o.kind == "door"]
                for a, b, _, _ in _wall_pieces(length, 0.08, [o.model_copy(update={"sill_m": 0.0, "head_m": 1.0})
                                                              for o in doors]):
                    c = (a + b) / 2 - length / 2
                    if along_x:
                        y = (d / 2 - 0.01) * (1 if wall == "north" else -1)
                        box(ws, (c, y, 0.04), (b - a, 0.02, 0.08), 1, bevel=0.0)
                    else:
                        x = (w / 2 - 0.01) * (1 if wall == "east" else -1)
                        box(ws, (x, c, 0.04), (0.02, b - a, 0.08), 1, bevel=0.0)
            obj = _mesh_object(f"WALL_{wall}", ws, [wall_mat, trim_mat])
            obj["studio_role"] = "wall"
            obj["studio_wall"] = wall
            if wall == "south":
                obj["studio_cutaway"] = True
                obj.visible_camera = False
            coll.objects.link(obj)
            info["walls"].append({"wall": wall, "openings": len(ops), "cutaway": wall == "south"})

    for item in env.dressing:
        obj = build_prop_object(item.prop, f"DRESS_{item.id}", material_prefix=f"{env.key}_{item.id}")
        obj.location = (item.at[0], item.at[1], item.z)
        obj.rotation_euler = (0, 0, math.radians(item.rotation_deg))
        obj["studio_role"] = "dressing"
        obj["studio_dressing_id"] = item.id
        obj["studio_on"] = item.on or ""
        coll.objects.link(obj)
        info["dressing"].append(item.id)

    for mk in env.marks:
        e = bpy.data.objects.new(f"MARK_{mk.name}", None)
        e.empty_display_type = "SINGLE_ARROW"
        e.empty_display_size = 0.3
        e.location = mk.at
        # the arrow points -Y at facing 0, like a character
        e.rotation_euler = (math.radians(-90), 0, math.radians(mk.facing_deg))
        e["studio_role"] = "mark"
        e["studio_mark_kind"] = mk.kind
        e["studio_facing_deg"] = mk.facing_deg
        coll.objects.link(e)
        info["marks"].append(mk.name)

    for cm in env.cameras:
        from mathutils import Vector

        data = bpy.data.cameras.new(f"CAM_{cm.name}")
        data.lens = cm.lens_mm
        data.clip_end = 300
        cam = bpy.data.objects.new(f"CAM_{cm.name}", data)
        cam.location = cm.at
        cam.rotation_euler = (Vector(cm.look_at) - Vector(cm.at)).to_track_quat("-Z", "Y").to_euler()
        cam["studio_role"] = "camera_mark"
        cam["studio_look_at"] = list(cm.look_at)
        coll.objects.link(cam)
        info["cameras"].append(cm.name)

    lights = lighting.build_rig(env.lighting, env.size, env.kind, env.key)
    bpy.context.scene.collection.children.link(lights)
    for target in (coll, lights):
        target["studio_environment_name"] = env.name
        target["studio_environment_digest"] = digest
        target["studio_template"] = env.template
        target["studio_style_profile"] = env.style_profile
        target["studio_kind"] = env.kind
        target["studio_size"] = list(env.size)
        target["studio_lighting"] = env.lighting.key
    info["lights"] = [o.name for o in lights.objects]
    return coll, lights, info
