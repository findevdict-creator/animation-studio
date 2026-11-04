"""Attaching an outfit to a character (architecture 9.2).

``attach_outfit`` appends the ``OUTFIT_*`` collection of an outfit file and fits it to one
character in the scene:

1. **Retarget**: every piece moves from the reference skeleton it was generated on to the
   character's skeleton (``fitspace``); rigid pieces keep their shape.
2. **Clear**: pieces are pushed out of the body and of the layers below, in layer order
   (footwear, lower, upper, headwear, accessories), then smoothed.
3. **Bind**: the Armature modifier targets the character's rig; weights are the outfit's
   own (rig-profile DEF bone names), rigid pieces are bound to their attachment bone.
4. **Mask**: body vertices under a garment and away from its hems are hidden by a Geometry
   Nodes modifier reading a per-vertex flag from a small mask object, so skin cannot show
   through cloth and nothing near an opening disappears.

Works on a character opened from its own file and on a linked character with a library
override: the garments and the mask object are local objects, and the body only gains a
modifier.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1, RigProfile

from cartoon_engine.character.skeleton import Skeleton
from cartoon_engine.character.validate import Parts
from cartoon_engine.outfit import fitspace
from cartoon_engine.outfit.garments import (
    Surface,
    clear_rigid_cover,
    clear_rigid_solid,
    fit_surface,
    mesh_arrays,
)

MASK_PREFIX = "outfit_mask_"


@dataclass
class Fitted:
    collection: object
    garments: list  # objects, in layer order
    hidden: np.ndarray  # per body vertex
    hidden_share: dict[str, float]  # per mask region
    pieces: dict[str, dict] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"body_vertices_hidden": int(self.hidden.sum()), "body_vertices": len(self.hidden),
                "hidden_share_by_region": self.hidden_share, "pieces": self.pieces}


def append_outfit(path: Path | str, scene=None):
    """Append (copy) the ``OUTFIT_*`` collection of ``path``: fitting changes geometry, so
    every wearer gets its own copy while the published file stays as it is."""
    import bpy

    scene = scene or bpy.context.scene
    with bpy.data.libraries.load(str(path), link=False) as (src, dst):
        names = [c for c in src.collections if c.startswith("OUTFIT_")]
        if len(names) != 1:
            raise ValueError(f"{path} has {len(names)} OUTFIT_ collections, expected 1")
        dst.collections = names
    coll = dst.collections[0]
    scene.collection.children.link(coll)
    return coll


def garments_of(coll) -> list:
    objs = [o for o in coll.all_objects if o.type == "MESH" and "studio_slot" in o]
    return sorted(objs, key=lambda o: (int(o["studio_layer"]), o.name))


def surfaces_of(parts: Parts) -> list[Surface]:
    out = []
    for obj in (parts.body, parts.head):
        co, polys = mesh_arrays(obj.data)
        m = np.array(obj.matrix_world)
        out.append(Surface(co @ m[:3, :3].T + m[:3, 3], polys, name=obj.name))
    return out


def rig_skeleton_mismatch(parts: Parts, sk: Skeleton) -> float:
    """Largest distance (m) between the skeleton's joints and the rig's deform bones: a
    character file and the character description it is fitted with must agree."""
    pairs = {"DEF-upper_arm.L": "shoulder.L", "DEF-forearm.L": "elbow.L", "DEF-hand.L": "wrist.L",
             "DEF-thigh.L": "hip.L", "DEF-shin.L": "knee.L", "DEF-foot.L": "ankle.L",
             "DEF-spine": "pelvis", "DEF-spine.004": "neck_base", "DEF-upper_arm.R": "shoulder.R"}
    m = np.array(parts.rig.matrix_world)
    worst = 0.0
    for bone, joint in pairs.items():
        head = m[:3, :3] @ np.array(parts.rig.data.bones[bone].head_local) + m[:3, 3]
        worst = max(worst, float(np.linalg.norm(head - np.array(sk.j(joint)))))
    return worst


def _set_co(obj, co: np.ndarray) -> None:
    obj.data.vertices.foreach_set("co", co.ravel())
    obj.data.update()


def bind_rigid(obj, rig, bone_name: str) -> None:
    """Parent to the attachment bone so the piece moves exactly with it (a bendy deform
    bone would bend a backpack). The inverse is taken from the bone's rest matrix, so it does
    not matter how the rig is posed when the outfit is attached."""
    from mathutils import Matrix

    bone = rig.data.bones[bone_name]
    obj.parent = rig
    obj.parent_type = "BONE"
    obj.parent_bone = bone_name
    rest_tail = rig.matrix_world @ bone.matrix_local @ Matrix.Translation((0.0, bone.length, 0.0))
    obj.matrix_parent_inverse = rest_tail.inverted()
    obj.matrix_basis = Matrix.Identity(4)
    for m in [m for m in obj.modifiers if m.type == "ARMATURE"]:
        obj.modifiers.remove(m)


def bind_skinned(obj, rig) -> None:
    mod = obj.modifiers.get("rig") or obj.modifiers.new("rig", "ARMATURE")
    mod.object = rig
    while obj.modifiers[0] != mod:
        obj.modifiers.move(obj.modifiers.find(mod.name), 0)
    obj.parent = rig
    obj.parent_type = "OBJECT"
    obj.matrix_parent_inverse.identity()


def fit_outfit(coll, parts: Parts, target: Skeleton, profile: RigProfile = CARTOON_HUMANOID_V1,
               min_clearance: float = 0.002, mask_margin: float = 0.025) -> Fitted:
    src = fitspace.skeleton_from_json(coll["studio_ref_skeleton"])
    body_surface, head_surface = surfaces_of(parts)
    under = [body_surface]  # skinned garments clear the body and the layers below, not the head
    garments = garments_of(coll)
    pieces: dict[str, dict] = {}
    for obj in garments:
        co0, polys = mesh_arrays(obj.data)
        kind, binding = obj["studio_kind"], obj["studio_binding"]
        clearance = max(float(obj["studio_clearance"]) * 0.5, min_clearance)
        info: dict = {"slot": obj["studio_slot"], "garment": obj["studio_garment"], "binding": binding}
        if binding == "skinned":
            co = fitspace.retarget(src, target, co0)
            co, pushed = fit_surface(co, polys, under, clearance)
            info["pushed_clear"] = pushed
        elif kind == "cover":  # a cap: head affine, then scaled until the head is inside
            co = fitspace.rigid_map(src, target, co0, np.array(obj["studio_anchor"]), use_head=True)
            co, scale = clear_rigid_cover(co, polys, np.array(target.head_center), [head_surface], clearance)
            info["scaled_up"] = round(scale, 4)
        else:  # a solid accessory: anchor through skeleton space, moved clear of body and clothes
            co = fitspace.rigid_map(src, target, co0, np.array(obj["studio_anchor"]), use_head=False)
            co, moved = clear_rigid_solid(co, polys, [*under, head_surface], clearance,
                                          direction=np.array([0.0, 1.0, 0.0]))
            info["moved_back_m"] = round(moved, 4)
        info["max_shift_m"] = round(float(np.linalg.norm(co - co0, axis=1).max()), 4)
        _set_co(obj, co)
        if kind == "cover" and binding == "skinned":
            under.append(Surface(co, polys, offset=float(obj["studio_thickness"]), name=obj.name))
        if binding == "rigid":
            bind_rigid(obj, parts.rig, obj["studio_attach_bone"])
        else:
            bind_skinned(obj, parts.rig)
        pieces[obj.name] = info

    hidden, cover = mask_body(parts, coll, garments, mask_margin)
    coll["studio_worn_by"] = parts.rig.name
    return Fitted(coll, garments, hidden, cover, pieces)


MASK_ATTRIBUTE = "studio_hide"


def _mask_node_group(mask_obj):
    """Geometry Nodes: delete the points whose ``studio_hide`` (read by index from a mask
    object with one point per body vertex) is set. Added after the Armature modifier, it
    works on a linked body too, where vertex groups cannot be added. One group per worn
    outfit, naming its mask object."""
    import bpy

    ng = bpy.data.node_groups.new(f"{mask_obj.name}_nodes", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n = ng.nodes
    gin, gout = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    info = n.new("GeometryNodeObjectInfo")
    info.transform_space = "ORIGINAL"
    info.inputs["Object"].default_value = mask_obj
    attr = n.new("GeometryNodeInputNamedAttribute")
    attr.data_type = "FLOAT"
    attr.inputs["Name"].default_value = MASK_ATTRIBUTE
    sample = n.new("GeometryNodeSampleIndex")
    sample.data_type = "FLOAT"
    sample.domain = "POINT"
    index = n.new("GeometryNodeInputIndex")
    cmp = n.new("FunctionNodeCompare")
    cmp.data_type = "FLOAT"
    cmp.operation = "GREATER_THAN"
    cmp.inputs[1].default_value = 0.5
    delete = n.new("GeometryNodeDeleteGeometry")
    delete.domain = "POINT"
    L = ng.links
    L.new(info.outputs["Geometry"], sample.inputs["Geometry"])
    L.new(attr.outputs["Attribute"], sample.inputs["Value"])
    L.new(index.outputs["Index"], sample.inputs["Index"])
    L.new(sample.outputs["Value"], cmp.inputs[0])
    L.new(gin.outputs["Geometry"], delete.inputs["Geometry"])
    L.new(cmp.outputs["Result"], delete.inputs["Selection"])
    L.new(delete.outputs["Geometry"], gout.inputs["Geometry"])
    return ng


def hidden_mask(parts: Parts) -> np.ndarray:
    """Per body vertex: hidden by the outfit currently attached (False if none)."""
    n = len(parts.body.data.vertices)
    for mod in parts.body.modifiers:
        if mod.name.startswith(MASK_PREFIX) and mod.type == "NODES" and mod.node_group is not None:
            info = next((nd for nd in mod.node_group.nodes if nd.bl_idname == "GeometryNodeObjectInfo"), None)
            obj = info.inputs["Object"].default_value if info else None
            if obj is not None and len(obj.data.vertices) == n:
                vals = np.empty(n)
                obj.data.attributes[MASK_ATTRIBUTE].data.foreach_get("value", vals)
                return vals > 0.5
    return np.zeros(n, dtype=bool)


def mask_body(parts: Parts, coll, garments: list, margin: float) -> tuple[np.ndarray, dict[str, float]]:
    """Hide body vertices that are under a garment and at least ``margin`` from its hems,
    so skin cannot show through cloth while nothing near an opening disappears."""
    import bpy
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    from cartoon_engine.outfit.garments import edges_of

    for mod in [m for m in parts.body.modifiers if m.name.startswith(MASK_PREFIX)]:
        parts.body.modifiers.remove(mod)
    body_co, _ = mesh_arrays(parts.body.data)
    hide = np.zeros(len(body_co), dtype=bool)
    for o in garments:
        if o["studio_kind"] != "cover" or o["studio_binding"] != "skinned":
            continue
        co, polys = mesh_arrays(o.data)
        shell = Surface(co, polys)
        edges, boundary = edges_of(polys)
        bverts = np.unique(edges[boundary].ravel())
        kd = KDTree(max(len(bverts), 1))
        for i, v in enumerate(bverts):
            kd.insert(co[v], i)
        kd.balance()
        for i, p in enumerate(body_co):
            if hide[i]:
                continue
            r = shell.signed(Vector(p), 0.06)
            if r is not None and r[0] < 0 and (not len(bverts) or kd.find(p)[2] >= margin):
                hide[i] = True
    # the mask object: one point per body vertex carrying the flag
    mesh = bpy.data.meshes.new(f"{coll.name}_mask")
    mesh.vertices.add(len(body_co))
    mesh.vertices.foreach_set("co", body_co.ravel())
    attr = mesh.attributes.new(MASK_ATTRIBUTE, "FLOAT", "POINT")
    attr.data.foreach_set("value", hide.astype(float))
    mask_obj = bpy.data.objects.new(f"{coll.name}_mask", mesh)
    mask_obj["studio_mask_for"] = parts.body.name
    mask_obj["studio_vertex_count"] = len(body_co)
    mask_obj.hide_render = True
    coll.objects.link(mask_obj)
    mod = parts.body.modifiers.new(f"{MASK_PREFIX}{coll.name}", "NODES")
    mod.node_group = _mask_node_group(mask_obj)
    # summary per region, from the body's region groups
    share = {}
    for g in parts.body.vertex_groups:
        if not g.name.startswith("mask_"):
            continue
        gi = g.index
        idx = [v.index for v in parts.body.data.vertices if any(e.group == gi and e.weight >= 0.5 for e in v.groups)]
        if idx and hide[idx].any():
            share[g.name[5:]] = round(float(hide[idx].mean()), 3)
    return hide, share


def attach_outfit(path: Path | str, parts: Parts, target: Skeleton, min_clearance: float = 0.002) -> Fitted:
    coll = append_outfit(path)
    return fit_outfit(coll, parts, target, min_clearance=min_clearance)


def detach_outfit(fitted: Fitted, parts: Parts) -> None:
    import bpy

    for mod in [m for m in parts.body.modifiers if m.name.startswith(MASK_PREFIX)]:
        if mod.node_group is not None:
            bpy.data.node_groups.remove(mod.node_group)
        parts.body.modifiers.remove(mod)
    for obj in list(fitted.collection.objects):
        bpy.data.objects.remove(obj)
    bpy.data.collections.remove(fitted.collection)


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "outfit"


def stamp_collection(coll, resolved, digest: str, ref_skeleton: Skeleton) -> None:
    coll["studio_outfit_name"] = resolved.name
    coll["studio_outfit_digest"] = digest
    coll["studio_rig_profile"] = resolved.rig_profile
    coll["studio_style_profile"] = resolved.style_profile
    coll["studio_fit_profile"] = resolved.primary_fit_profile
    coll["studio_fit_profiles"] = list(resolved.fit_profiles)
    coll["studio_catalog"] = resolved.catalog
    coll["studio_scope"] = resolved.scope
    coll["studio_hides"] = resolved.hides
    coll["studio_covers"] = resolved.covers
    coll["studio_ref_skeleton"] = fitspace.skeleton_json(ref_skeleton)
    coll["studio_pieces"] = json.dumps([p.model_dump() for p in resolved.pieces], sort_keys=True)
