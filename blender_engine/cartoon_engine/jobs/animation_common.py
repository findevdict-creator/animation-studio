"""Shared by the animation jobs: make the Action, describe it, check it on reference bodies,
render a preview strip.

The check plays the saved ``animation.blend`` (not the Action in memory) on freshly built,
rigged reference characters: the reference fit profile, plus the other declared profiles
(or, when the animation is declared for every profile, one adult body, so a different set of
proportions is always tried). On each body the Action is fitted exactly as a scene fits it
(``cartoon_engine.animation.apply``) and measured (``cartoon_engine.animation.measure``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from studio_contracts.animation import (
    AnimationCompat,
    ResolvedAnimation,
    compatibility_issues,
)
from studio_contracts.character import resolve_spec
from studio_contracts.outfit import reference_character
from studio_contracts.rig_profile import BUILTIN_RIG_PROFILES

from cartoon_engine.animation import apply as A
from cartoon_engine.animation import measure as M
from cartoon_engine.animation import rig as R
from cartoon_engine.animation.generators import fcurves
from cartoon_engine.core import blend_io

#: what the check accepts
MAX_SLIDE_CM = {"generator": 1.5, "bvh": 3.0}
MAX_STRETCH = 1.02
MAX_SINK_M = 0.01
MAX_LOOP_DEG = 1.0
MIN_MOTION_DEG = 2.0
OTHER_BODY = "adult_average"


def reference_body(fit_profile: str, style_ref: str):
    """A rigged reference character of ``fit_profile`` at the origin; returns (rc, parts)."""
    from cartoon_engine.character.build import build_geometry
    from cartoon_engine.character.rig import build_rig
    from cartoon_engine.character.validate import find_parts

    rc = resolve_spec(reference_character(fit_profile, style_ref))
    ref = build_geometry(rc, collection_name=f"CHAR_REF_{fit_profile}")
    build_rig(ref)
    return rc, find_parts(ref.collection)


def keyed(action) -> tuple[list[str], list[str], list[str]]:
    """(pose bones keyed, face properties keyed, switch bones keyed) of an Action."""
    bones, props, switches = set(), set(), set()
    for fc in fcurves(action):
        path = fc.data_path
        if not path.startswith('pose.bones["'):
            continue
        bone = path.split('"')[1]
        if path.endswith('["IK_FK"]'):
            switches.add(bone)
        elif '"]["' in path:
            props.add(path.split('"')[3])
        else:
            bones.add(bone)
    return sorted(bones | switches), sorted(props), sorted(switches)


def make_action(ra: ResolvedAnimation, rig, ws: Path) -> tuple[Any, dict, float]:
    """Bake or retarget; returns (action, build info, speed per frame on this body)."""
    from cartoon_engine.animation import generators as G
    from cartoon_engine.animation import retarget as RT

    if ra.source_kind == "generator":
        gen = G.make(ra, rig)
        action = G.bake(rig, gen, ra.action_name)
        return action, {"generator": ra.generator, "frames": gen.frames, "params": ra.params}, gen.speed
    src = ws / "inputs" / "source.bvh"
    action, info = RT.retarget(rig, ra, src, ra.action_name)
    info["ground_shift_m"] = RT.ground(rig, action, info["frames"])
    speed = info["travel_m"] / max(info["frames"], 1)
    if ra.travels:
        planted = RT.plant_feet(rig, action, info["frames"], speed)
        speed = planted.pop("speed")
        info["plant"] = planted
        info["travel_m"] = round(speed * info["frames"], 4)
    return action, info, speed


def describe(ra: ResolvedAnimation, rig, action, frames: int, speed: float) -> AnimationCompat:
    controls, props, _ = keyed(action)
    geo = R.leg_geometry(rig)
    return AnimationCompat(rig_profile=ra.rig_profile, category=ra.category, frames=frames, loops=ra.loops,
                           travels=ra.travels and speed > 0, controls=tuple(controls), face_props=tuple(props),
                           fit_profiles=ra.fit_profiles, reference_fit_profile=ra.reference_fit_profile,
                           reference_leg_length_m=round(geo.leg_length, 5),
                           stride_m=round(speed * frames, 5) if ra.travels else 0.0)


def stamp(action, ra: ResolvedAnimation, compat: AnimationCompat, digest: str) -> None:
    action["studio_animation_digest"] = digest
    action["studio_animation_name"] = ra.name
    action["studio_rig_profile"] = ra.rig_profile
    action["studio_compat"] = compat.model_dump_json()
    action.use_fake_user = True


def save_action(action, path: Path) -> None:
    import bpy

    bpy.data.libraries.write(str(path), {action}, fake_user=True)


def check_bodies(ra: ResolvedAnimation) -> list[str]:
    declared = list(ra.fit_profiles) or [ra.reference_fit_profile, OTHER_BODY]
    bodies = [ra.reference_fit_profile, *[p for p in declared if p != ra.reference_fit_profile]]
    return bodies[:3]


def _rest_z(rig, point: str) -> float:
    """Rest height of a measured foot point (``ball.L`` ...): how far above the floor it is
    when the foot stands flat; going lower means the foot is in the floor."""
    name, side = point.split(".")
    bone, end = {n: (b, e) for n, b, e in M.POINTS}[name]
    b = rig.data.bones[f"{bone}.{side}"]
    return float((b.head_local if end == "head" else b.tail_local).z)


def play_and_measure(blend: Path, ra: ResolvedAnimation, compat: AnimationCompat, fit_profile: str) -> dict:
    """Build the body, fit the saved Action to it and measure it."""
    import bpy

    blend_io.reset_empty()
    rc, parts = reference_body(fit_profile, ra.style_profile)
    rig = parts.rig
    action = A.append_action(blend, f"check:{fit_profile}")
    ratio = A.wearer_ratio(rig, compat)
    A.scale_locations(action, ratio)
    rig.animation_data_create()
    rig.animation_data.action = action
    if action.slots:
        rig.animation_data.action_slot = action.slots[0]
    scene = bpy.context.scene
    frames = list(range(compat.frames + 1))
    speed = compat.stride_m * ratio / compat.frames if compat.travels else 0.0
    tracks = M.foot_tracks(rig, scene, frames, travel=(lambda f: (0.0, -speed * f, 0.0)) if speed else None)
    slides = {k: M.slide_cm(v) for k, v in tracks.items()}
    sink = max(_rest_z(rig, k) - float(t[:, 2].min()) for k, t in tracks.items())
    bones = [c for c in compat.controls if "IK_FK" not in c and c in rig.pose.bones and not c.endswith("_parent.L")
             and not c.endswith("_parent.R")]
    motion = M.control_motion(rig, scene, bones, frames) if bones else {}
    face = rig.pose.bones[BUILTIN_RIG_PROFILES[ra.rig_profile].face_control_bone]
    peaks = {}
    for prop in compat.face_props:
        vals = []
        for f in frames:
            scene.frame_set(f)
            vals.append(float(face.get(prop, 0.0)))
        peaks[prop] = round(max(vals), 3)
    out = {"fit_profile": fit_profile, "height_m": rc.height_m, "leg_length_m": round(R.leg_geometry(rig).leg_length, 4),
           "leg_ratio": round(ratio, 4), "travel_m": round(speed * compat.frames, 4), "foot_slide_cm": slides,
           "max_slide_cm": max(slides.values()) if slides else 0.0, "leg_stretch": M.leg_stretch(rig, scene, frames),
           "lowest_foot_below_rest_m": round(sink, 4),
           "loop_error_deg": M.loop_error_deg(rig, scene, bones, 0, compat.frames) if compat.loops and bones else 0.0,
           "control_motion_deg": motion, "face_peaks": peaks,
           "compatibility": compatibility_issues(compat, ra.rig_profile, fit_profile)}
    rig.animation_data.action = None
    return out


def judge(ra: ResolvedAnimation, compat: AnimationCompat, plays: list[dict], file_ok: bool) -> dict:
    checks: list[dict] = [{"name": "animation_file_alone", "ok": file_ok}]
    deform = [c for c in compat.controls if c.startswith(("DEF-", "ORG-", "MCH-"))]
    checks.append({"name": "keys_controls_only", "ok": not deform, "deform_bones": deform})
    limit = MAX_SLIDE_CM[ra.source_kind]
    for p in plays:
        fp = p["fit_profile"]
        moved = max(p["control_motion_deg"].values(), default=0.0)
        checks += [
            {"name": f"compatible:{fp}", "ok": not p["compatibility"], "problems": p["compatibility"]},
            {"name": f"foot_slide:{fp}", "ok": p["max_slide_cm"] <= limit, "max_cm": p["max_slide_cm"],
             "limit_cm": limit},
            {"name": f"leg_stretch:{fp}", "ok": p["leg_stretch"] <= MAX_STRETCH, "value": p["leg_stretch"],
             "limit": MAX_STRETCH},
            {"name": f"feet_above_floor:{fp}", "ok": p["lowest_foot_below_rest_m"] <= MAX_SINK_M,
             "below_rest_m": p["lowest_foot_below_rest_m"], "limit_m": MAX_SINK_M},
            {"name": f"moves:{fp}", "ok": moved >= MIN_MOTION_DEG or any(v >= 0.5 for v in p["face_peaks"].values()),
             "largest_rotation_deg": moved, "face_peaks": p["face_peaks"]},
        ]
        if compat.loops:
            checks.append({"name": f"loops:{fp}", "ok": p["loop_error_deg"] <= MAX_LOOP_DEG,
                           "error_deg": p["loop_error_deg"], "limit_deg": MAX_LOOP_DEG})
    failed = [c["name"] for c in checks if not c["ok"]]
    return {"ok": not failed, "failed": failed, "checks": checks, "bodies": plays,
            "thresholds": {"max_slide_cm": limit, "max_stretch": MAX_STRETCH, "max_sink_m": MAX_SINK_M,
                           "max_loop_deg": MAX_LOOP_DEG, "min_motion_deg": MIN_MOTION_DEG}}


def preview_strip(blend: Path, ra: ResolvedAnimation, compat: AnimationCompat, out: Path, tile: int, samples: int,
                  count: int = 6) -> tuple[Path, Path, list[int]]:
    """Frames across the clip on the reference body (in place), side-on for locomotion and
    three-quarter for the rest; returns (strip, thumbnail, frames)."""
    import bpy

    from cartoon_engine.character import previews as P
    from cartoon_engine.scene.assemble import contact_sheet

    blend_io.reset_empty()
    rc, parts = reference_body(ra.reference_fit_profile, ra.style_profile)
    rig = parts.rig
    action = A.append_action(blend, "preview")
    rig.animation_data_create()
    rig.animation_data.action = action
    if action.slots:
        rig.animation_data.action_slot = action.slots[0]
    frames = sorted({int(round(i * compat.frames / max(count - 1, 1))) for i in range(count)})
    opts = P.RenderOptions(resolution=tile, samples=samples)
    scene, cam = P.setup_scene(opts)
    meshes = parts.meshes()
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for f in frames:
        scene.frame_set(f)
        a, b = P._bounds(meshes)
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    angle = 90.0 if ra.category == "locomotion" else 25.0
    P._frame(cam, lo, hi, angle)
    tmp = out.parent / "tmp_frames"
    tmp.mkdir(exist_ok=True)
    paths = []
    for f in frames:
        scene.frame_set(f)
        p = tmp / f"f{f:04d}.png"
        P._render_png(scene, p, tile, tile)
        paths.append(p)
    contact_sheet(paths, out)
    thumb = out.with_name("thumbnail.png")
    thumb.write_bytes(paths[len(paths) // 2].read_bytes())
    rig.animation_data.action = None
    bpy.context.view_layer.update()
    return out, thumb, frames


def compat_from_file(blend: Path) -> tuple[AnimationCompat | None, dict]:
    import bpy

    with bpy.data.libraries.load(str(blend), link=True) as (src, dst):
        dst.actions = list(src.actions)
    stamps = {}
    for a in dst.actions:
        if a is not None:
            stamps = {k: a[k] for k in a.keys() if k.startswith("studio_")}
    raw = stamps.get("studio_compat")
    return (AnimationCompat.model_validate(json.loads(raw)) if raw else None), stamps
