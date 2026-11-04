"""Loading published characters into a scene (architecture 7.2).

A character version is linked from its own library ``character.blend`` and given a library
override, so the scene owns only the override (pose, face controls) while geometry, rig and
materials stay in the published file. Two versions of one character, or two characters,
can sit in one scene: each links from a different file, and Blender keeps their data apart
even though the object names inside both files are the same.
"""

from __future__ import annotations

from pathlib import Path


def link_character(path: Path | str, scene=None):
    """Link the ``CHAR_*`` collection of ``path`` into ``scene`` with an editable library
    override; returns the override collection."""
    import bpy

    scene = scene or bpy.context.scene
    path = str(path)
    before = {c for c in bpy.data.collections}
    with bpy.data.libraries.load(path, link=True, relative=False) as (src, dst):
        names = [c for c in src.collections if c.startswith("CHAR_")]
        if len(names) != 1:
            raise ValueError(f"{path} has {len(names)} CHAR_ collections, expected 1")
        dst.collections = names
    linked = next(c for c in bpy.data.collections if c not in before and c.library is not None
                  and Path(bpy.path.abspath(c.library.filepath)).resolve() == Path(path).resolve())
    view_layer = bpy.context.view_layer
    override = linked.override_hierarchy_create(scene, view_layer, do_fully_editable=True)
    if override.name not in scene.collection.children:
        scene.collection.children.link(override)
    view_layer.update()
    return override


def library_of(collection) -> str | None:
    """The library file an override collection's data comes from."""
    ref = collection.override_library.reference if collection.override_library else None
    return ref.library.filepath if ref is not None and ref.library is not None else None
