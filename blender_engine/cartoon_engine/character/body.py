"""Body mesh: a skin-modifier skeleton (torso, neck, arms, hands with five fingers, legs,
feet) evaluated, subdivided once and baked to a plain mesh. The head is a separate object
(``head.py``) that hides the neck top."""

from __future__ import annotations

from cartoon_engine.character.skeleton import FINGERS, Skeleton

SPINE = ("pelvis", "spine1", "spine2", "chest", "neck_base", "neck_mid", "head_base")


def _chains(sk: Skeleton) -> list[list[str]]:
    chains = [list(SPINE)]
    for s in "LR":
        chains.append(["chest", f"shoulder.{s}", f"elbow.{s}", f"wrist.{s}", f"palm.{s}"])
        for f in FINGERS:
            start = f"wrist.{s}" if f == "thumb" else f"palm.{s}"
            chains.append([start, *(f"{f}_{k}.{s}" for k in range(4))])
        chains.append(["pelvis", f"hip.{s}", f"knee.{s}", f"ankle.{s}", f"ball.{s}", f"toe.{s}"])
        chains.append([f"ankle.{s}", f"heel.{s}"])
    return chains


def build_body(sk: Skeleton, materials: dict, name: str = "body"):
    import bpy

    names: list[str] = []
    index: dict[str, int] = {}
    edges: list[tuple[int, int]] = []
    for chain in _chains(sk):
        for a, b in zip(chain, chain[1:]):
            for n in (a, b):
                if n not in index:
                    index[n] = len(names)
                    names.append(n)
            edges.append((index[a], index[b]))
    verts = [sk.joints[n] for n in names]

    mesh = bpy.data.meshes.new(f"{name}_skeleton")
    mesh.from_pydata(verts, edges, [])
    obj = bpy.data.objects.new(f"{name}_skeleton", mesh)
    bpy.context.scene.collection.objects.link(obj)
    skin = obj.modifiers.new("skin", "SKIN")
    skin.branch_smoothing = 0.6
    skin.use_smooth_shade = True
    data = mesh.skin_vertices[0].data
    for i, n in enumerate(names):
        rad = sk.radii.get(n)
        if rad is None:  # in-between joints (clavicle etc.) take their neighbours' size
            rad = (0.02 * sk.height, 0.02 * sk.height)
        data[i].radius = rad
        data[i].use_root = n == "pelvis"
    sub = obj.modifiers.new("subsurf", "SUBSURF")
    sub.levels = 2
    sub.render_levels = 2

    depsgraph = bpy.context.evaluated_depsgraph_get()
    baked = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    baked.name = name
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)

    body = bpy.data.objects.new(name, baked)
    bpy.context.scene.collection.objects.link(body)
    for p in baked.polygons:
        p.use_smooth = True
    baked.materials.append(materials["skin"])
    baked.materials.append(materials["base_suit"])
    _assign_suit(sk, baked)
    return body


def _assign_suit(sk: Skeleton, mesh) -> None:
    """Base-suit material on the torso and the upper half of the thighs (a tank top and
    shorts), decided by which limb segment owns each face."""
    from cartoon_engine.character.skeleton import nearest_segments, segments

    segs = segments(sk)
    top = sk.j("shoulder.L")[2] - 0.01 * sk.height
    for p in mesh.polygons:
        c = tuple(p.center)
        (_, (bone, region, _, _), t), = nearest_segments(sk, c, segs, k=1)
        torso = region == "torso" and not bone.startswith("DEF-shoulder") and c[2] < top
        p.material_index = 1 if torso or (region == "thigh" and t < 0.5) else 0
