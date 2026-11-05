"""Reading and linking prop and environment files, the way scenes use them."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def file_contents(path: Path) -> dict[str, Any]:
    """What a file holds, read without loading it."""
    import bpy

    with bpy.data.libraries.load(str(path), link=True) as (src, _):
        return {"collections": sorted(src.collections), "objects": len(src.objects), "meshes": len(src.meshes),
                "armatures": list(src.armatures), "lights": len(src.lights), "cameras": len(src.cameras),
                "materials": len(src.materials)}


def prop_file_alone(c: dict[str, Any]) -> bool:
    return (not c["armatures"] and len(c["collections"]) == 1 and c["collections"][0].startswith("PROP_")
            and c["objects"] == 1 and c["meshes"] == 1 and not c["lights"] and not c["cameras"])


def environment_file_alone(c: dict[str, Any]) -> bool:
    names = c["collections"]
    return (not c["armatures"] and len(names) == 2 and sum(n.startswith("ENV_") for n in names) == 1
            and sum(n.startswith("LIGHTS_") for n in names) == 1 and c["lights"] >= 2)


def link_collections(path: Path, prefixes: tuple[str, ...], parent=None) -> dict[str, Any]:
    """Link the collections of ``path`` whose names start with ``prefixes`` under ``parent``
    (the scene collection by default); returns them by prefix."""
    import bpy

    with bpy.data.libraries.load(str(path), link=True, relative=True) as (src, dst):
        dst.collections = [n for n in src.collections if n.startswith(prefixes)]
    parent = parent or bpy.context.scene.collection
    out = {}
    for c in dst.collections:
        parent.children.link(c)
        out[next(p for p in prefixes if c.name.startswith(p))] = c
    return out


def stamps(idblock) -> dict[str, Any]:
    out = {}
    for k in idblock.keys():
        if k.startswith("studio_"):
            v = idblock[k]
            out[k] = v.to_list() if hasattr(v, "to_list") else v
    return out
