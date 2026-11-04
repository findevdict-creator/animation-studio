"""Opening and saving .blend files the same way in every job."""

from __future__ import annotations

from pathlib import Path


def reset_empty() -> None:
    """Start from an empty file with factory settings, independent of any user config."""
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)


def save(path: Path) -> Path:
    import bpy

    path.parent.mkdir(parents=True, exist_ok=True)
    # relative_remap keeps library and image paths relative to the saved file (architecture 7.1)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False, relative_remap=True, compress=False)
    return path


def open_file(path: Path) -> None:
    import bpy

    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)


def blender_version() -> str:
    import bpy

    return bpy.app.version_string
