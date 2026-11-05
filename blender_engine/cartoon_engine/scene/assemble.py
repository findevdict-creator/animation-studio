"""Assembling character instances into a scene (architecture 7.2, SRS 16.2).

For every instance of a ``ResolvedScene``, in a fresh file:

1. link the character's ``CHAR_`` collection from its pinned ``character.blend`` and make a
   library override (rig pose, face controls, modifiers become local; meshes stay linked);
2. append the pinned outfit and fit it to the override (fitting moves garment vertices, so
   each wearer gets its own copy; the outfit file is unchanged and the copy is stamped with
   its source ``OUTFIT-002@1.0#sha256``), with its body mask;
3. link each pinned accessory's mesh and attach it to its attachment bone;
4. run the accessory attach check on this character, at rest, before any animation;
5. assign clips (NLA strips) and the face track (expressions, blinks, dialogue mouth);
6. place the instance: the override rig's object transform.

Everything an instance owns sits in a local collection ``INST_<key>``, so a reopened scene
finds each instance by its manifest key; the manifest, not the file, is authoritative (D5).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from studio_contracts.accessory import get_accessory_conventions
from studio_contracts.character import ResolvedCharacter
from studio_contracts.outfit import get_conventions
from studio_contracts.rig_profile import BUILTIN_RIG_PROFILES
from studio_contracts.scene import ResolvedInstance, ResolvedScene

INSTANCE_PREFIX = "INST_"


@dataclass
class Assembled:
    key: str
    collection: object
    override: object
    parts: object
    garments: list = field(default_factory=list)
    accessories: list = field(default_factory=list)
    report: dict = field(default_factory=dict)


def _matrix(t) -> object:
    from mathutils import Euler, Matrix

    rot = Euler(tuple(math.radians(a) for a in t.rotation_deg), "XYZ").to_matrix().to_4x4()
    return Matrix.Translation(t.location) @ rot @ Matrix.Scale(t.scale, 4)


def link_character_into(path: Path, inst_coll, scene):
    """Link ``CHAR_*`` from ``path`` and override it under ``inst_coll``. Works for the same
    character file several times in one scene: each call makes its own override."""
    import bpy

    path = Path(path).resolve()
    with bpy.data.libraries.load(str(path), link=True, relative=True) as (src, dst):
        names = [c for c in src.collections if c.startswith("CHAR_")]
        if len(names) != 1:
            raise ValueError(f"{path} has {len(names)} CHAR_ collections, expected 1")
        dst.collections = list(names)
    linked = next(c for c in bpy.data.collections if c.name == names[0] and c.library is not None
                  and c.override_library is None
                  and Path(bpy.path.abspath(c.library.filepath)).resolve() == path)
    override = linked.override_hierarchy_create(scene, bpy.context.view_layer, do_fully_editable=True)
    if override.name in scene.collection.children:
        scene.collection.children.unlink(override)
    if override.name not in inst_coll.children:
        inst_coll.children.link(override)
    for c in list(scene.collection.children):  # the linked original, if Blender instanced it
        if c == linked:
            scene.collection.children.unlink(c)
    bpy.context.view_layer.update()
    return override


def assemble_instance(ws: Path, inst: ResolvedInstance, scene, *, check_accessories: bool = True) -> Assembled:
    import bpy

    from cartoon_engine.accessory.attach import attach_accessory
    from cartoon_engine.accessory.check import attach_check
    from cartoon_engine.character.skeleton import build_skeleton
    from cartoon_engine.character.validate import find_parts
    from cartoon_engine.outfit.fit import append_outfit, fit_outfit
    from cartoon_engine.scene.clips import assign, use_fk
    from cartoon_engine.scene.face import build_face_track

    coll = bpy.data.collections.new(f"{INSTANCE_PREFIX}{inst.key}")
    scene.collection.children.link(coll)
    coll["studio_instance"] = inst.key
    char_dir = ws / inst.character.path
    override = link_character_into(char_dir / "character.blend", coll, scene)
    parts = find_parts(override)
    rc = ResolvedCharacter.model_validate(json.loads((char_dir / "character.json").read_text())["resolved"])
    sk = build_skeleton(rc)
    profile = BUILTIN_RIG_PROFILES[inst.rig_profile]
    report: dict = {"key": inst.key, "character": {"ref": inst.character.ref, "name": inst.character.name,
                                                     "override": override.name, "rig": parts.rig.name,
                                                     "library": parts.body.data.library.filepath
                                                     if parts.body.data.library else None}}
    out = Assembled(inst.key, coll, override, parts, report=report)

    if inst.outfit is not None:
        conv = get_conventions(rc.style_profile)
        o_coll = append_outfit(ws / inst.outfit.path / "outfit.blend", scene)
        scene.collection.children.unlink(o_coll)
        coll.children.link(o_coll)
        fitted = fit_outfit(o_coll, parts, sk, profile, min_clearance=conv.min_clearance_m,
                            mask_margin=conv.mask_margin_m)
        source = f"{inst.outfit.ref}#{inst.outfit.files['outfit.blend']}"
        for idb in [o_coll, *o_coll.all_objects]:
            idb["studio_source"] = source
            idb["studio_instance"] = inst.key
        out.garments = fitted.garments
        report["outfit"] = {"ref": inst.outfit.ref, "collection": o_coll.name, "source": source,
                            "pieces": [g.name for g in fitted.garments],
                            "body_vertices_hidden": int(fitted.hidden.sum()),
                            "compatibility": inst.outfit_compatibility}

    acc_conv = get_accessory_conventions(rc.style_profile)
    for use in inst.accessories:
        att = attach_accessory(ws / use.pin.path / "accessory.blend", parts, sk, point=use.attach_point,
                               bone=use.attach_bone, collection=coll, name=f"{inst.key}:{use.pin.code}",
                               garments=out.garments, clearance=acc_conv.thresholds.clearance_m)
        att.obj["studio_source"] = f"{use.pin.ref}#{use.pin.files['accessory.blend']}"
        att.obj["studio_instance"] = inst.key
        out.accessories.append(att)
    if out.accessories:
        report["accessories"] = [{"ref": u.pin.ref, "object": a.obj.name, **a.info}
                                 for u, a in zip(inst.accessories, out.accessories, strict=True)]
        if check_accessories:
            r, per_pose = attach_check(parts, out.accessories, acc_conv.thresholds, acc_conv.poses, profile)
            report["accessory_check"] = {"ok": r.ok, "failed": r.failed(), "checks": r.checks}

    use_fk(parts.rig)
    report["animations"] = assign(parts.rig, inst.animations, scene.render.fps, inst.key, ws=ws)
    report["face"] = build_face_track(parts.rig, inst, scene.render.fps, scene.frame_start, scene.frame_end, profile)
    # placement last: binding and checks above assume the rig at its rest placement
    parts.rig.matrix_world = _matrix(inst.transform)
    bpy.context.view_layer.update()
    report["transform"] = inst.transform.model_dump(mode="json")
    report["state"] = dict(inst.state)
    return out


def assemble(ws: Path, rs: ResolvedScene, progress=None) -> tuple[list[Assembled], object]:
    import bpy

    from cartoon_engine.core import blend_io

    blend_io.reset_empty()
    scene = bpy.context.scene
    scene.name = "SCENE"
    scene.render.fps = rs.fps
    scene.frame_start, scene.frame_end = rs.frame_start, rs.frame_end
    scene["studio_manifest_digest"] = rs.manifest_digest
    built = []
    for i, inst in enumerate(rs.instances):
        if progress:
            progress(i / max(len(rs.instances), 1), f"assembling {inst.key} ({inst.character.ref})")
        built.append(assemble_instance(ws, inst, scene))
    add_dialogue_audio(ws, rs, scene)
    scene.frame_set(rs.frame_start)
    return built, scene


def add_dialogue_audio(ws: Path, rs: ResolvedScene, scene) -> list[dict]:
    """A sound strip per recorded line, one channel per instance, so a preview or the edit
    plays the dialogue the mouths were synced to."""
    out = []
    lines = [(i, inst, d) for i, inst in enumerate(rs.instances) for d in inst.dialogue if d.audio is not None]
    if not lines:
        return out
    se = scene.sequence_editor_create()
    strips = se.strips if hasattr(se, "strips") else se.sequences
    for i, inst, d in lines:
        strip = strips.new_sound(f"{inst.key}:{d.audio.code}", str((ws / d.audio.path).resolve()), channel=1 + i,
                                 frame_start=d.start_frame)
        strip["studio_audio"] = f"{d.audio.code}#{d.audio.sha256}"
        out.append({"strip": strip.name, "frame_start": d.start_frame, "audio": d.audio.code})
    scene["studio_dialogue_audio"] = len(out)
    return out


# ------------------------------------------------------------------ camera and light
def stage_camera(scene, built: list[Assembled], rs: ResolvedScene):
    """A fixed camera and two suns framing the cast (or the focus instance) over the whole
    scene: the bounds are taken at frames across it, so a character that walks stays in shot."""
    import bpy

    from cartoon_engine.character import previews as P

    targets = [b for b in built if rs.camera.focus in (None, b.key)]
    objs = [o for b in targets for o in (*b.parts.meshes(), *b.garments, *(a.obj for a in b.accessories))]
    span = max(rs.frame_end - rs.frame_start, 0)
    samples = sorted({rs.frame_start + round(span * k / 8) for k in range(9)})
    boxes = []
    for f in samples:
        scene.frame_set(f)
        boxes.append(P._bounds(objs))
    scene.frame_set(rs.frame_start)
    lo = np.min([np.asarray(b[0]) for b in boxes], axis=0)
    hi = np.max([np.asarray(b[1]) for b in boxes], axis=0)
    if rs.camera.preset != "wide":
        top = hi[2]
        share = 0.55 if rs.camera.preset == "medium" else 0.3
        lo = lo.copy()
        lo[2] = top - (hi[2] - lo[2]) * share
    cam_data = bpy.data.cameras.new("CAM_main")
    cam_data.lens = 40.0
    cam = bpy.data.objects.new("CAM_main", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    P._frame(cam, lo, hi, rs.camera.angle_deg, margin=1.2, aspect=16 / 9, elevation_deg=8.0)
    _fit_box(scene, cam, lo, hi)
    for name, energy, rot in (("SUN_key", 3.0, (50, 0, -35)), ("SUN_rim", 1.4, (60, 0, 160))):
        data = bpy.data.lights.new(name, "SUN")
        data.energy = energy
        obj = bpy.data.objects.new(name, data)
        obj.rotation_euler = tuple(math.radians(a) for a in rot)
        scene.collection.objects.link(obj)
    world = bpy.data.worlds.new("WORLD_scene")
    scene.world = world
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.78, 0.8, 0.84, 1.0)
    bg.inputs[1].default_value = 0.9
    floor = bpy.data.meshes.new("FLOOR")
    size = float(max(hi[0] - lo[0], hi[1] - lo[1], 2.0)) * 2
    c = (lo + hi) / 2
    floor.from_pydata([(c[0] - size, c[1] - size, 0), (c[0] + size, c[1] - size, 0), (c[0] + size, c[1] + size, 0),
                       (c[0] - size, c[1] + size, 0)], [], [(0, 1, 2, 3)])
    fobj = bpy.data.objects.new("FLOOR", floor)
    mat = bpy.data.materials.new("FLOOR")
    mat.diffuse_color = (0.62, 0.64, 0.66, 1)
    if mat.node_tree is None:
        mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.55, 0.57, 0.6, 1)
    floor.materials.append(mat)
    scene.collection.objects.link(fobj)
    return cam


def _fit_box(scene, cam, lo: np.ndarray, hi: np.ndarray, border: float = 0.04) -> None:
    """Back the camera off along its view line until every corner of the box is in shot. The
    first guess treats the box as flat; a walk toward the camera is deep, and its near end
    would be cut off."""
    import itertools

    import bpy
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector

    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080  # the 16:9 the previews render at
    center = Vector(((lo + hi) / 2).tolist())
    corners = [Vector(c) for c in itertools.product(*zip(lo.tolist(), hi.tolist(), strict=True))]
    for _ in range(40):
        bpy.context.view_layer.update()
        views = [world_to_camera_view(scene, cam, c) for c in corners]
        if all(border <= v.x <= 1 - border and border <= v.y <= 1 - border and v.z > 0 for v in views):
            return
        cam.location = center + (cam.location - center) * 1.08


def render_frames(scene, out_dir: Path, frames: list[int], res: tuple[int, int], samples: int) -> list[Path]:
    from cartoon_engine.character import previews as P

    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    try:
        scene.cycles.use_denoising = True
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
    except (TypeError, AttributeError):
        scene.cycles.use_denoising = False
    scene.view_settings.view_transform = "Standard"
    paths = []
    for f in frames:
        scene.frame_set(f)
        p = out_dir / f"frame_{f:04d}.png"
        P._render_png(scene, p, *res)
        paths.append(p)
    return paths


def contact_sheet(paths: list[Path], out: Path, labels: list[str] | None = None) -> Path:
    """Frames side by side in one PNG (numpy, no PIL in Blender's Python)."""
    from cartoon_engine.character import previews as P

    imgs = [P._load(p) for p in paths]
    h = max(i.shape[0] for i in imgs)
    sheet = np.concatenate([np.pad(i, ((0, h - i.shape[0]), (0, 4), (0, 0)), constant_values=1.0) for i in imgs], axis=1)
    P._save(sheet, out)
    return out
