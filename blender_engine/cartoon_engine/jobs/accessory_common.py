"""Shared by the accessory jobs: build on the reference body, attach, check, previews."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from studio_contracts.accessory import ResolvedAccessory, get_accessory_conventions
from studio_contracts.character import resolve_spec
from studio_contracts.outfit import reference_character

import cartoon_engine
from cartoon_engine.core import blend_io


def accessory_file_contents(path: Path) -> dict[str, Any]:
    """What an accessory file holds, read without loading it: one ACC_ collection with one
    object and its mesh, materials, no armature and no character."""
    import bpy

    with bpy.data.libraries.load(str(path), link=True) as (src, _):
        return {"collections": list(src.collections), "objects": list(src.objects), "meshes": list(src.meshes),
                "armatures": list(src.armatures), "materials": len(src.materials)}


def file_is_alone(contents: dict[str, Any]) -> bool:
    return (not contents["armatures"] and len(contents["collections"]) == 1
            and contents["collections"][0].startswith("ACC_") and len(contents["objects"]) == 1
            and len(contents["meshes"]) == 1 and contents["meshes"][0].startswith("ACC_"))


def reference_wearer(ra: ResolvedAccessory, rigged: bool = True):
    """A freshly built reference character of the accessory's reference fit profile."""
    from cartoon_engine.character.build import build_geometry
    from cartoon_engine.character.rig import build_rig
    from cartoon_engine.character.validate import Parts, find_parts

    rc = resolve_spec(reference_character(ra.reference_fit_profile, ra.style_profile))
    ref = build_geometry(rc, collection_name=f"CHAR_REF_{ra.reference_fit_profile}")
    if not rigged:
        return rc, ref, Parts(rig=None, body=ref.body, head=ref.head, eyes=ref.eyes, hair=ref.hair)
    build_rig(ref)
    return rc, ref, find_parts(ref.collection)


def attach_and_check(blend: Path, parts, sk, ra: ResolvedAccessory, point: str | None = None):
    from cartoon_engine.accessory.attach import attach_accessory
    from cartoon_engine.accessory.check import attach_check

    conv = get_accessory_conventions(ra.style_profile)
    att = attach_accessory(blend, parts, sk, point=point, clearance=conv.thresholds.clearance_m)
    report, per_pose = attach_check(parts, [att], conv.thresholds, conv.poses)
    doc = {"ok": report.ok, "failed": report.failed(), "checks": report.checks, "poses": per_pose,
           "placement": att.info, "thresholds": conv.thresholds.model_dump(), "pose_suite": list(conv.poses),
           "blender_version": blend_io.blender_version(), "engine_version": cartoon_engine.__version__}
    return doc, att


def close_up(parts, obj, out: Path, opts, angle: float = 30.0) -> Path:
    """The item and the part of the wearer around it."""
    from cartoon_engine.character import previews as P

    scene, cam = P.setup_scene(opts)
    lo, hi = P._bounds([obj])
    c, half = (lo + hi) / 2, max(float((hi - lo).max()) * 0.9, 0.12)
    P._frame(cam, c - half, c + half, angle)
    P._render_png(scene, out, opts.resolution, opts.resolution)
    return out


def full_body(parts, obj, out: Path, opts, angle: float = 30.0) -> Path:
    from cartoon_engine.character import previews as P

    scene, cam = P.setup_scene(opts)
    lo, hi = P._bounds([*parts.meshes(), obj])
    P._frame(cam, np.asarray(lo), np.asarray(hi), angle)
    P._render_png(scene, out, opts.resolution, opts.resolution)
    return out
