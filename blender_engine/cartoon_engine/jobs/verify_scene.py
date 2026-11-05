"""``verify_scene``: reopen a saved scene in a fresh workspace and check it against its
manifest (SRS Phase 5 "reopen the scenes", "the source character asset remains unchanged").

The worker rebuilt the workspace from the manifest alone: the pinned files under
``assets/`` and the stored ``scene.blend`` at ``scene/scene.blend`` (a different directory
from the one it was saved in). Opened with scripts disabled, the file must:

* resolve every library through its relative path, all of them pinned files;
* hold every instance under ``INST_<key>``: a library override of the pinned character,
  the outfit's garments bound to that instance's rig and stamped with the pinned source,
  each accessory's mesh linked from its pinned file and parented to the right bone;
* place each rig where the manifest says;
* play the face track: at every expression key the face control properties have the
  manifest's values and the face geometry moves by the expression's weight; clips are NLA strips at their frames;
* render a frame;
* leave every pinned source byte-identical (hashed before and after).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from studio_contracts.jobs import VerifySceneParams
from studio_contracts.rig_profile import BUILTIN_RIG_PROFILES
from studio_contracts.scene import ResolvedScene

import cartoon_engine
from cartoon_engine.core import blend_io
from cartoon_engine.core.context import JobContext, write_json

TOL = 1e-4



def _from_library(idb) -> bool:
    """Linked from a library, or a library override of linked data (the head mesh is one:
    its Key and drivers have to follow the override rig). Either way the geometry lives in
    the pinned character file, not a copy in the scene."""
    if idb.library is not None:
        return True
    ol = idb.override_library
    return ol is not None and ol.reference is not None and ol.reference.library is not None


def _lib_of(idb):
    lib = idb.library or (idb.override_library.reference.library if idb.override_library else None)
    return lib.filepath if lib else None


class _shape_keys_only:
    """Turn the head's modifiers off so its evaluated vertices are basis plus shape keys."""

    def __init__(self, obj):
        self.obj, self.saved = obj, []

    def __enter__(self):
        self.saved = [(m, m.show_viewport) for m in self.obj.modifiers]
        for m, _ in self.saved:
            m.show_viewport = False
        return self

    def __exit__(self, *exc):
        for m, v in self.saved:
            m.show_viewport = v


def _measured_weight(obj, key_name: str) -> float | None:
    import bpy

    key = obj.data.shape_keys
    kb = key.key_blocks.get(key_name) if key else None
    if kb is None:
        return None
    n = len(kb.data)
    basis, target = np.empty(n * 3), np.empty(n * 3)
    kb.relative_key.data.foreach_get("co", basis)
    kb.data.foreach_get("co", target)
    d = (target - basis).reshape(-1, 3)
    moved = np.linalg.norm(d, axis=1) > 1e-5
    if not moved.any():
        return None
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        cur = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", cur)
    finally:
        ev.to_mesh_clear()
    disp = (cur - basis).reshape(-1, 3)[moved]
    dm = d[moved]
    return float((disp * dm).sum() / (dm * dm).sum())

def _matrix_close(a, b, tol: float = 1e-4) -> bool:
    return bool(np.abs(np.array(a) - np.array(b)).max() < tol)


def check_instance(inst, scene, ws) -> list[dict]:
    import bpy

    from cartoon_engine.character.validate import find_parts
    from cartoon_engine.scene.assemble import INSTANCE_PREFIX, _matrix
    from cartoon_engine.scene.face import expression_values

    checks: list[dict] = []

    def add(name: str, ok: bool, **detail) -> None:
        checks.append({"name": f"{inst.key}:{name}", "ok": bool(ok), **detail})

    coll = bpy.data.collections.get(f"{INSTANCE_PREFIX}{inst.key}")
    add("instance_collection", coll is not None)
    if coll is None:
        return checks
    overrides = [c for c in coll.children if c.override_library is not None]
    add("character_is_a_library_override", len(overrides) == 1, overrides=[c.name for c in overrides])
    if len(overrides) != 1:
        return checks
    parts = find_parts(overrides[0])
    char_lib = parts.body.data.library
    expected_char = f"{inst.character.path}/character.blend"
    add("character_linked_from_pin", char_lib is not None and bpy.path.abspath(char_lib.filepath).replace("\\", "/")
        .endswith(expected_char), library=char_lib.filepath if char_lib else None, pin=inst.character.ref)
    add("character_mesh_not_copied", all(_from_library(m.data) for m in (parts.body, parts.head)),
        body=_lib_of(parts.body.data), head=_lib_of(parts.head.data))
    profile = BUILTIN_RIG_PROFILES[inst.rig_profile]

    if inst.outfit is not None:
        source = f"{inst.outfit.ref}#{inst.outfit.files['outfit.blend']}"
        outfits = [c for c in coll.children if c.name.startswith("OUTFIT_")]
        add("outfit_present", len(outfits) == 1 and outfits[0].get("studio_source") == source, source=source)
        if outfits:
            garments = [o for o in outfits[0].all_objects if o.type == "MESH" and "studio_slot" in o]
            bound = all((o.parent == parts.rig) and (o.get("studio_binding") == "rigid"
                        or any(m.type == "ARMATURE" and m.object == parts.rig for m in o.modifiers)) for o in garments)
            add("outfit_bound_to_this_rig", bound and bool(garments), pieces=sorted(o.name for o in garments))
            add("body_mask_present", any(m.name.startswith("outfit_mask_") for m in parts.body.modifiers))
    for use in inst.accessories:
        name = f"{inst.key}:{use.pin.code}"
        obj = bpy.data.objects.get(name)
        lib = obj.data.library if obj is not None else None
        add(f"accessory:{use.pin.code}", obj is not None and lib is not None
            and bpy.path.abspath(lib.filepath).replace("\\", "/").endswith(f"{use.pin.path}/accessory.blend")
            and obj.parent == parts.rig and obj.parent_type == "BONE" and obj.parent_bone == use.attach_bone,
            bone=obj.parent_bone if obj else None, expected_bone=use.attach_bone)
        if use.placement == "cover" and use.attach_point == "head_top" and parts.hair is not None:
            from cartoon_engine.accessory.attach import HAIR_MASK_PREFIX

            mod = parts.hair.modifiers.get(f"{HAIR_MASK_PREFIX}{name}")
            add(f"hair_under_hat:{use.pin.code}", mod is not None and mod.node_group is not None,
                modifier=mod.name if mod else None)
    add("placement", _matrix_close(parts.rig.matrix_world, _matrix(inst.transform)),
        location=[round(v, 4) for v in parts.rig.matrix_world.translation])

    # face track: the control properties at every key, and the face geometry they move. The
    # shape-key ``value`` of an override's Key is not written back after a reopen (the
    # driven value lives only in the evaluated mesh), so the weight is measured on the
    # evaluated head with its modifiers off: projected displacement along the key's offset.
    pb = parts.rig.pose.bones[profile.face_control_bone]
    bad = []
    with _shape_keys_only(parts.head):
        for e in inst.expressions:
            scene.frame_set(e.frame)
            bpy.context.view_layer.update()
            for prop, value in expression_values(e.expression, e.weight, profile).items():
                got = float(pb.get(prop, -1.0))
                if abs(got - value) > 1e-3:
                    bad.append({"frame": e.frame, "prop": prop, "want": value, "got": round(got, 4)})
            if e.expression != "neutral":
                # the brow key: the mouth key is damped by dialogue visemes, the eyes by blinks
                name = f"expr_{e.expression}_brow"
                got = _measured_weight(parts.head, name)
                if got is None or abs(got - e.weight) > 0.1:
                    bad.append({"frame": e.frame, "shape_key": name, "want": e.weight,
                                "measured": None if got is None else round(got, 3)})
    add("expressions_play", not bad, keys=len(inst.expressions), mismatches=bad[:10])
    ad = parts.rig.animation_data
    strips = {(t.name, s.action.name if s.action else None, round(s.frame_start, 3))
              for t in (ad.nla_tracks if ad else []) for s in t.strips}
    want = {(f"{i:02d}_{u.key}", f"{inst.key}:{u.clip if u.source == 'library' else u.key}", float(u.start_frame))
            for i, u in enumerate(inst.animations)}
    add("animations_assigned", want <= strips, want=sorted(map(list, want)), found=sorted(map(list, strips)))
    checks.extend(_library_animation_checks(inst, scene, parts, add))
    if inst.dialogue:
        d = inst.dialogue[0]
        frame = d.start_frame
        pool = ("vis_AI", "vis_O", "vis_E", "vis_MBP")
        if d.audio is not None:
            # the first open-mouth cue of the recording, a frame into it
            cue = next((c for c in d.audio.cues if c.viseme not in ("rest", "MBP") and c.end_s - c.start_s > 1.5 / scene.render.fps), None)
            if cue is not None:
                frame = int(round(d.start_frame + cue.end_s * scene.render.fps - 1))
                pool = (f"vis_{cue.viseme}",)
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        props = {v: float(pb.get(v, 0.0)) for v in pool}
        active = max(props, key=props.get)
        with _shape_keys_only(parts.head):
            bpy.context.view_layer.update()
            moved = _measured_weight(parts.head, active)
        # the visemes share mouth vertices, so the projection is approximate: it must show
        # the mouth clearly open, not match the property exactly
        add("dialogue_mouth_moves", props[active] > 0.5 and moved is not None and moved > 0.3,
            frame=frame, viseme=active, prop=round(props[active], 3), audio=d.audio.code if d.audio else None,
            measured=None if moved is None else round(moved, 3))
    scene.frame_set(scene.frame_start)
    return checks


def _library_animation_checks(inst, scene, parts, add) -> list[dict]:
    """Each library animation: appended from its pin and fitted to this rig; a travelling one
    moves the root by the stride the feet imply, and its planted feet stay put in the world."""
    import bpy
    from studio_contracts.animation import AnimationCompat

    from cartoon_engine.animation import measure as M
    from cartoon_engine.animation.apply import wearer_ratio
    from cartoon_engine.jobs.animation_common import MAX_SLIDE_CM

    rig = parts.rig
    for i, u in enumerate(inst.animations):
        if u.source != "library":
            continue
        name = f"{inst.key}:{u.clip}"
        act = bpy.data.actions.get(name)
        compat = AnimationCompat.model_validate(u.compat)
        ratio = wearer_ratio(rig, compat)
        src = f"{u.pin.ref}#{u.pin.files['animation.blend']}"
        add(f"animation:{u.clip}", act is not None and act.get("studio_source") == src
            and abs(float(act.get("studio_scale", 0)) - ratio) < 1e-4, source=act.get("studio_source") if act else None,
            leg_ratio=round(ratio, 4))
        track = rig.animation_data.nla_tracks.get(f"{i:02d}_{u.key}") if rig.animation_data else None
        if track is None or not track.strips or not (u.travel and compat.travels):
            continue
        strip = track.strips[0]
        a, b = int(math.ceil(strip.frame_start)), int(math.floor(min(strip.frame_end, scene.frame_end)))

        def root_at(f):
            scene.frame_set(f)
            return rig.matrix_world @ rig.pose.bones["root"].matrix.translation
        moved = (root_at(b) - root_at(a)).length
        expected = compat.stride_m * ratio * u.repeat * (b - a) / max(strip.frame_end - strip.frame_start, 1e-6)
        add(f"travels:{u.clip}", abs(moved - expected) <= 0.02 * max(expected, 0.1) + 0.01, moved_m=round(moved, 4),
            expected_m=round(expected, 4), frames=[a, b])
        # measured where the strip plays at full weight (its blend in and out ease the feet),
        # in the frames the scene shows; a foot point is planted near the lowest it gets over
        # the whole strip, as the build check measures it, even when the scene ends mid-strip
        full = [f for f in range(a, int(math.floor(strip.frame_end)) + 1)
                if strip.frame_start + strip.blend_in <= f <= strip.frame_end - strip.blend_out]
        tracks = M.foot_tracks(rig, scene, full)
        shown = np.array([f <= scene.frame_end for f in full])
        slides = {k: M.slide_cm(t[shown], floor=float(t[:, 2].min())) for k, t in tracks.items()} if shown.any() else {}
        inner = [f for f, s in zip(full, shown, strict=True) if s]
        # a generated cycle is held to the tighter limit it was published under
        limit = MAX_SLIDE_CM["generator" if act is not None and act.get("studio_generator") else "bvh"]
        worst = max(slides.values(), default=0.0)
        add(f"feet_planted:{u.clip}", worst <= limit, max_slide_cm=worst, limit_cm=limit,
            slide_cm={k: round(v, 2) for k, v in slides.items()},
            frames=[inner[0], inner[-1]] if inner else None)
    scene.frame_set(scene.frame_start)
    return []


def _dialogue_audio_checks(rs: ResolvedScene, scene) -> list[dict]:
    """Every recorded line has its sound strip, stored relative to the scene file, opening
    the staged recording the scene version froze, at the line's first frame."""
    import hashlib

    import bpy

    want = {f"{inst.key}:{d.audio.code}": (d, i) for i, inst in enumerate(rs.instances) for d in inst.dialogue
            if d.audio is not None}
    if not want:
        return []
    se = scene.sequence_editor
    strips = {s.name: s for s in ((se.strips_all if hasattr(se, "strips_all") else se.sequences_all) if se else [])
              if s.type == "SOUND"}
    out = []
    for name, (d, _) in sorted(want.items()):
        st = strips.get(name)
        path = Path(bpy.path.abspath(st.sound.filepath)) if st is not None and st.sound else None
        sha = hashlib.sha256(path.read_bytes()).hexdigest() if path is not None and path.is_file() else None
        out.append({"name": f"dialogue_audio:{name}", "ok": st is not None and sha == d.audio.sha256
                    and st.get("studio_audio") == f"{d.audio.code}#{d.audio.sha256}"
                    and int(st.frame_start) == d.start_frame and st.sound.filepath.startswith("//"),
                    "stored": st.sound.filepath if st is not None and st.sound else None, "sha256": sha,
                    "frame_start": int(st.frame_start) if st is not None else None})
    return out


def run(ctx: JobContext) -> dict[str, Any]:
    from cartoon_engine.jobs.assemble_scene import preview_frames
    from cartoon_engine.scene import files
    from cartoon_engine.scene.assemble import contact_sheet, render_frames

    params = VerifySceneParams.model_validate(ctx.envelope.params)
    rs = ResolvedScene.model_validate(params.resolved)
    ws, out = ctx.workspace, ctx.out_dir
    blend = ws / "scene" / "scene.blend"
    if files.sha256(blend) != params.scene_blend_sha256:
        raise RuntimeError("scene/scene.blend is not the stored scene file")
    before = files.hash_sources(ws, rs)
    if before != files.expected_files(rs):
        raise RuntimeError("a staged file does not match its pin")
    ctx.progress(0.1, "opening scene.blend")
    blend_io.open_file(blend)
    import bpy

    scene = bpy.context.scene
    checks: list[dict] = []
    libs = files.library_check(blend.parent, ws, rs)
    missing = [lib.filepath for lib in bpy.data.libraries if lib.is_missing]
    checks.append({"name": "libraries_resolve", "ok": libs["ok"] and not missing, "missing": missing,
                   "libraries": libs["libraries"]})
    checks.extend(_dialogue_audio_checks(rs, scene))
    checks.append({"name": "manifest_digest", "ok": scene.get("studio_manifest_digest") == rs.manifest_digest})
    checks.append({"name": "frame_range", "ok": (scene.frame_start, scene.frame_end, scene.render.fps)
                   == (rs.frame_start, rs.frame_end, rs.fps)})
    for i, inst in enumerate(rs.instances):
        ctx.progress(0.15 + 0.5 * i / len(rs.instances), f"checking {inst.key}")
        checks.extend(check_instance(inst, scene, ws))
    ctx.progress(0.7, "rendering from the reopened file")
    tmp = ws / "tmp" / "frames"
    tmp.mkdir(parents=True, exist_ok=True)
    frames = preview_frames(rs, params.previews.frames)
    paths = render_frames(scene, tmp, frames, (params.previews.width, params.previews.height), params.previews.samples)
    contact_sheet(paths, out / "reopened_frames.png")
    ctx.output(out / "reopened_frames.png", "preview", "image/png")
    checks.append({"name": "renders", "ok": all(p.exists() and p.stat().st_size > 0 for p in paths), "frames": frames})
    ok_src, changed = files.unchanged(ws, rs)
    checks.append({"name": "sources_unchanged", "ok": ok_src, "changed": changed,
                   "files": {k: v for k, v in files.hash_sources(ws, rs).items()}})
    failed = [c["name"] for c in checks if not c["ok"]]
    doc = {"scene": rs.name, "manifest_digest": rs.manifest_digest, "scene_blend_sha256": params.scene_blend_sha256,
           "opened_from": "scene/scene.blend", "ok": not failed, "failed": failed, "checks": checks,
           "sources": {"before": before, "unchanged": ok_src}, "blender_version": blend_io.blender_version(),
           "engine_version": cartoon_engine.__version__}
    write_json(out / "reopen_report.json", doc)
    ctx.output(out / "reopen_report.json", "report", "application/json")
    ctx.progress(1.0, "reopened and checked" if doc["ok"] else "reopen check failed")
    return {"ok": doc["ok"], "failed": failed, "checks": len(checks), "sources_unchanged": ok_src, "report": doc}



