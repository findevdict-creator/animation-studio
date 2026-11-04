"""Build an outfit's pieces on the reference body of its primary fit profile and gather
them in an ``OUTFIT_*`` collection ready to save (no rig, no character inside)."""

from __future__ import annotations

from studio_contracts.outfit import ResolvedOutfit

from cartoon_engine.character.build import Character
from cartoon_engine.outfit import garments as G
from cartoon_engine.outfit.fit import safe_name, stamp_collection


def build_outfit(ro: ResolvedOutfit, ref: Character, digest: str):
    """Returns (collection, per-piece stats)."""
    import bpy

    body_co, body_polys = G.mesh_arrays(ref.body.data)
    head_co, head_polys = G.mesh_arrays(ref.head.data)
    body = G.Surface(body_co, body_polys, name="body")
    head = G.Surface(head_co, head_polys, name="head")
    under = [body]  # skinned garments clear the body and lower layers; the cap clears the head
    coll = bpy.data.collections.new(f"OUTFIT_{safe_name(ro.name)}")
    bpy.context.scene.collection.children.link(coll)
    stats = {}
    for piece in sorted(ro.pieces, key=lambda p: G.LAYER[p.slot]):
        if piece.key == "cap":
            built = G.build_cap(piece, ref.skeleton, [head])
        elif piece.key == "backpack":
            built = G.build_backpack(piece, ref.skeleton, body_co, under)
        else:
            built = G.build_skinned(piece, ref.skeleton, body_co, body_polys, under)
        obj = G.make_object(built)
        coll.objects.link(obj)
        if built.kind == "cover":
            under.append(G.Surface(built.co, built.polys, offset=piece.thickness, name=obj.name))
        stats[obj.name] = built.stats
    stamp_collection(coll, ro, digest, ref.skeleton)
    return coll, stats
