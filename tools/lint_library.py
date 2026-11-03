"""Lint the asset library against library/CONVENTIONS.md.

    blender -b --python tools/lint_library.py -- --library library
    python -m tools.lint_library --library library

Exit code 1 on any error. Warnings are printed but do not fail.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from tools.blend_common import armature, kind_from_path, open_blend, scene_prop, script_args, single_action

ID_RX = {
    "character": r"^lib_char_[a-z0-9_]+$",
    "prop": r"^lib_prop_[a-z0-9_]+$",
    "environment": r"^lib_env_[a-z0-9_]+$",
    "clip": r"^clip_[a-z0-9_]+$",
    "base": r"^base_[a-z0-9_]+$",
    "garment": r"^garm_[a-z0-9_]+$",
    "hair": r"^hair_[a-z0-9_]+$",
    "kit_piece": r"^kit_[a-z0-9_]+$",
    "generator": r"^gen_[a-z0-9_]+$",
}
RIG_TYPES = {"rigify_human", "rigify_quadruped", "rigify_bird", "custom"}
REQUIRED_PROPS = {
    "character": ["studio_rig_type"],
    "base": ["studio_rig_type", "studio_proportion_sliders", "studio_outfit_slots"],
    "clip": ["studio_rig_type", "studio_licence"],
    "garment": ["studio_slot", "studio_fits"],
    "hair": ["studio_fits"],
    "kit_piece": ["studio_kit"],
    "generator": ["studio_generator_kind"],
}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def lint_file(library: Path, file: Path, report: Report) -> None:
    import bpy

    kind = kind_from_path(library, file)
    rel = file.relative_to(library).as_posix()
    if kind is None:
        report.warn(f"{rel}: not in a known library folder, ignored by the scanner")
        return
    if not re.match(ID_RX[kind], file.stem):
        report.error(f"{rel}: file name does not match the {kind} id pattern")
    open_blend(file)
    scene = bpy.context.scene
    if scene.unit_settings.system != "METRIC" or abs(scene.unit_settings.scale_length - 1.0) > 1e-6:
        report.error(f"{rel}: units must be metric with scale_length 1")
    if scene.render.fps != 24:
        report.warn(f"{rel}: scene fps is {scene.render.fps}, library standard is 24")
    for obj in bpy.data.objects:
        if obj.type in ("MESH", "ARMATURE") and any(abs(s - 1.0) > 1e-4 for s in obj.scale):
            report.error(f"{rel}: object {obj.name} has unapplied scale {tuple(round(s, 3) for s in obj.scale)}")
    for prop in REQUIRED_PROPS.get(kind, []):
        if scene_prop(prop) is None:
            report.warn(f"{rel}: missing scene property {prop}")
    rig_type = scene_prop("studio_rig_type")
    if rig_type is not None and rig_type not in RIG_TYPES:
        report.error(f"{rel}: unknown studio_rig_type {rig_type!r}")
    if kind in ("character", "base"):
        rig = armature()
        if rig is None:
            report.error(f"{rel}: no armature found")
        elif rig.name != "rig":
            report.warn(f"{rel}: armature object is named {rig.name!r}, convention is 'rig'")
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        if not meshes:
            report.error(f"{rel}: no mesh geometry")
        lowest = min((min((o.matrix_world @ _v(c)).z for c in o.bound_box) for o in meshes), default=0.0)
        if abs(lowest) > 0.02:
            report.error(f"{rel}: geometry does not sit on the ground (lowest point z={lowest:.3f})")
    if kind == "clip":
        action = single_action()
        if action is None:
            report.error(f"{rel}: a clip file must contain exactly one Action")
        elif action.name != file.stem:
            report.error(f"{rel}: Action is named {action.name!r}, must equal the file name")
    if kind == "environment":
        if not any(o.type == "EMPTY" and o.name.startswith("anchor_") for o in bpy.data.objects):
            report.warn(f"{rel}: environment has no anchor_* empties")


def _v(c):
    from mathutils import Vector

    return Vector(c)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="library")
    args = parser.parse_args(script_args(argv))
    library = Path(args.library).resolve()
    report = Report()
    files = [f for f in sorted(library.rglob("*.blend")) if "preview" not in f.parts and "source" not in f.parts]
    for file in files:
        lint_file(library, file, report)
    for w in report.warnings:
        print(f"warning: {w}")
    for e in report.errors:
        print(f"error: {e}")
    print(f"{len(files)} files, {len(report.errors)} errors, {len(report.warnings)} warnings")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
