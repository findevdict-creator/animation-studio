"""Preview renders and the browser GLB, made from the saved character file.

Nothing here is saved back into the asset: cameras, lights and labels live only in the
preview session. Cycles on the CPU is the default because it needs no GPU or display (Eevee
needs EGL, which headless workers do not have); MP4 encoding uses Blender's own FFmpeg
through the sequencer, so no external binary is needed.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cartoon_engine.character.validate import Parts

SHEET_KEYS = (
    ("neutral", {}),
    *((e, {f"expr_{e}": 1.0}) for e in ("happy", "sad", "angry", "surprised", "fearful", "confused", "excited",
                                        "laughing", "crying")),
    ("blink", {"eye_blink_L": 1.0, "eye_blink_R": 1.0}),
    ("viseme AI", {"vis_AI": 1.0}),
    ("viseme O", {"vis_O": 1.0}),
    ("viseme E", {"vis_E": 1.0}),
    ("viseme MBP", {"vis_MBP": 1.0}),
    ("viseme U", {"vis_U": 1.0}),
)


@dataclass
class RenderOptions:
    resolution: int = 512
    samples: int = 16
    turntable_frames: int = 24
    sheet_tile: int = 256
    focal_length_mm: float = 50.0
    fps: int = 24


def _bounds(objs) -> tuple[np.ndarray, np.ndarray]:
    import bpy

    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        co = np.empty(len(me.vertices) * 3)
        me.vertices.foreach_get("co", co)
        ev.to_mesh_clear()
        co = co.reshape(-1, 3)
        m = np.array(o.matrix_world)
        pts.append(co @ m[:3, :3].T + m[:3, 3])
    allp = np.vstack(pts)
    return allp.min(axis=0), allp.max(axis=0)


def setup_scene(opts: RenderOptions):
    import bpy

    scene = bpy.context.scene
    from cartoon_engine.core.device import use_cycles_device

    use_cycles_device(scene, samples=opts.samples)
    scene.render.film_transparent = False
    scene.render.fps = opts.fps
    scene.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("preview_world")
    scene.world = world
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.78, 0.8, 0.84, 1.0)
    bg.inputs[1].default_value = 0.9

    def light(name, kind, energy, rot, size=1.0, color=(1, 1, 1)):
        data = bpy.data.lights.new(name, kind)
        data.energy = energy
        data.color = color
        if kind == "AREA":
            data.size = size
        obj = bpy.data.objects.new(name, data)
        scene.collection.objects.link(obj)
        obj.rotation_euler = tuple(math.radians(a) for a in rot)
        return obj

    light("key", "SUN", 3.2, (50, 0, -35), color=(1.0, 0.97, 0.92))
    light("rim", "SUN", 1.6, (60, 0, 160), color=(0.9, 0.95, 1.0))
    cam_data = bpy.data.cameras.new("preview_camera")
    cam_data.lens = opts.focal_length_mm
    cam = bpy.data.objects.new("preview_camera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return scene, cam


def _frame(cam, lo: np.ndarray, hi: np.ndarray, angle_deg: float, margin: float = 1.15, aspect: float = 1.0,
           elevation_deg: float = 6.0) -> None:
    from mathutils import Vector

    center = (lo + hi) / 2
    height = float(hi[2] - lo[2]) * margin
    width = float(max(hi[0] - lo[0], hi[1] - lo[1])) * margin
    fov = 2 * math.atan(cam.data.sensor_width / (2 * cam.data.lens))
    span = max(height, width / aspect)
    dist = span / 2 / math.tan(fov / 2) + float(max(hi[1] - lo[1], hi[0] - lo[0])) / 2
    a, el = math.radians(angle_deg), math.radians(elevation_deg)
    cam.location = (center[0] + dist * math.sin(a) * math.cos(el), center[1] - dist * math.cos(a) * math.cos(el),
                    center[2] + dist * math.sin(el))
    direction = Vector(center.tolist()) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _render_png(scene, path: Path, res_x: int, res_y: int) -> None:
    import bpy

    scene.render.resolution_x, scene.render.resolution_y = res_x, res_y
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def _load(path: Path) -> np.ndarray:
    import bpy

    img = bpy.data.images.load(str(path))
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)


def _save(arr: np.ndarray, path: Path) -> None:
    import bpy

    h, w = arr.shape[:2]
    img = bpy.data.images.new(path.stem, w, h, alpha=True)
    img.pixels.foreach_set(np.ascontiguousarray(arr, dtype=np.float32).ravel())
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def turntable(parts: Parts, out_dir: Path, opts: RenderOptions, progress=None) -> dict:
    """Frames orbiting the character, a thumbnail (3/4 view), a contact sheet and an MP4."""
    scene, cam = setup_scene(opts)
    lo, hi = _bounds(parts.meshes())
    frames_dir = out_dir / "turntable_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(opts.turntable_frames):
        _frame(cam, lo, hi, 360.0 * i / opts.turntable_frames)
        p = frames_dir / f"{i:04d}.png"
        _render_png(scene, p, opts.resolution, opts.resolution)
        frames.append(p)
        if progress:
            progress(i + 1, opts.turntable_frames)
    _frame(cam, lo, hi, 30.0)
    thumb = out_dir / "thumbnail.png"
    _render_png(scene, thumb, opts.resolution, opts.resolution)

    picks = [frames[round(k * len(frames) / 8) % len(frames)] for k in range(8)]
    tiles = [_load(p) for p in picks]
    sheet = np.concatenate([np.concatenate(tiles[4:], axis=1), np.concatenate(tiles[:4], axis=1)], axis=0)
    sheet_path = out_dir / "turntable_sheet.png"
    _save(sheet, sheet_path)
    mp4 = out_dir / "turntable.mp4"
    encode_mp4(frames, mp4, opts.fps, opts.resolution)
    return {"frames": len(frames), "thumbnail": thumb, "sheet": sheet_path, "mp4": mp4, "bounds": [lo.tolist(), hi.tolist()]}


def encode_mp4(frames: list[Path], path: Path, fps: int, size: int, height: int | None = None) -> None:
    """Image strips through the sequencer into H.264 with Blender's bundled FFmpeg."""
    import bpy

    scene = bpy.data.scenes.new("encode")
    scene.render.resolution_x, scene.render.resolution_y = size, height or size
    scene.render.resolution_percentage = 100
    scene.render.fps = fps
    se = scene.sequence_editor_create()
    strip = se.strips.new_image("frames", str(frames[0]), channel=1, frame_start=1)
    for f in frames[1:]:
        strip.elements.append(f.name)
    scene.frame_start, scene.frame_end = 1, len(frames)
    r = scene.render
    if hasattr(r.image_settings, "media_type"):  # Blender 5: images and video are separate media types
        r.image_settings.media_type = "VIDEO"
    r.image_settings.file_format = "FFMPEG"
    r.ffmpeg.format = "MPEG4"
    r.ffmpeg.codec = "H264"
    r.ffmpeg.constant_rate_factor = "HIGH"
    r.ffmpeg.ffmpeg_preset = "GOOD"
    r.ffmpeg.gopsize = fps
    r.use_sequencer = True
    r.filepath = str(path)
    window_scene = bpy.context.window.scene if bpy.context.window else None
    with bpy.context.temp_override(scene=scene):
        bpy.ops.render.render(animation=True, scene=scene.name)
    if window_scene is not None:
        bpy.context.window.scene = window_scene
    bpy.data.scenes.remove(scene)
    # Blender appends the frame range to movie names unless the path has an extension it keeps
    if not path.exists():
        candidates = sorted(path.parent.glob(path.stem + "*.mp4"))
        if candidates:
            candidates[0].rename(path)
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("MP4 encoding produced no file")


def expression_sheet(parts: Parts, out_dir: Path, opts: RenderOptions, face_bone: str = "face_ctrl") -> dict:
    """A labelled grid of the face: neutral, every expression, a blink and five visemes."""
    import bpy

    from cartoon_engine.character.validate import _update

    scene, cam = setup_scene(opts)
    head_objs = [o for o in (parts.head, parts.eyes["L"], parts.eyes["R"], parts.hair) if o is not None]
    lo, hi = _bounds(head_objs)
    lo = lo.copy()
    lo[2] -= (hi[2] - lo[2]) * 0.18  # room for the label under the chin
    _frame(cam, lo, hi, 12.0, margin=1.08, elevation_deg=2.0)
    label_data = bpy.data.curves.new("label", "FONT")
    label_data.align_x = "CENTER"
    label_data.size = float(hi[2] - lo[2]) * 0.08
    label = bpy.data.objects.new("label", label_data)
    scene.collection.objects.link(label)
    mat = bpy.data.materials.new("label")
    mat.diffuse_color = (0.05, 0.05, 0.08, 1)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.03, 0.03, 0.05, 1)
    label_data.materials.append(mat)
    center = (lo + hi) / 2
    label.location = (center[0] + 0.0, float(lo[1]) - 0.02, float(lo[2]) + (hi[2] - lo[2]) * 0.03)
    label.rotation_euler = (math.radians(90), 0, cam.rotation_euler[2])
    ctrl = parts.rig.pose.bones[face_bone]
    tiles = []
    tmp = out_dir / "sheet_tiles"
    tmp.mkdir(parents=True, exist_ok=True)
    for i, (name, values) in enumerate(SHEET_KEYS):
        for k in list(ctrl.keys()):
            if isinstance(ctrl[k], float):
                ctrl[k] = 0.0
        for k, v in values.items():
            ctrl[k] = v
        label_data.body = name
        _update(parts)
        p = tmp / f"{i:02d}.png"
        _render_png(scene, p, opts.sheet_tile, opts.sheet_tile)
        tiles.append(_load(p))
    for k in list(ctrl.keys()):
        if isinstance(ctrl[k], float):
            ctrl[k] = 0.0
    _update(parts)
    cols = 4
    rows = math.ceil(len(tiles) / cols)
    blank = np.ones_like(tiles[0])
    while len(tiles) < rows * cols:
        tiles.append(blank)
    grid_rows = [np.concatenate(tiles[r * cols:(r + 1) * cols], axis=1) for r in range(rows)]
    sheet = np.concatenate(list(reversed(grid_rows)), axis=0)  # image rows run bottom-up
    path = out_dir / "expressions.png"
    _save(sheet, path)
    bpy.data.objects.remove(label)
    return {"path": path, "tiles": [n for n, _ in SHEET_KEYS]}


def export_glb(parts: Parts, path: Path) -> dict:
    """Browser preview: DEF bones only, shape keys as morph targets, no animation."""
    import bpy

    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in [parts.rig, *parts.meshes()]:
        o.hide_set(False)
        o.select_set(True)
    bpy.context.view_layer.objects.active = parts.rig
    bpy.ops.export_scene.gltf(
        filepath=str(path), export_format="GLB", use_selection=True, export_apply=False,
        export_morph=True, export_morph_normal=False, export_skins=True, export_def_bones=True,
        export_animations=False, export_yup=True, export_materials="EXPORT", export_cameras=False,
        export_lights=False, export_extras=False,
    )
    return inspect_glb(path)


def inspect_glb(path: Path) -> dict:
    """Read the GLB's JSON chunk: meshes, morph target names, skin joints."""
    data = path.read_bytes()
    magic, version, _ = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2:
        raise RuntimeError("not a glTF 2 binary")
    length, kind = struct.unpack_from("<I4s", data, 12)
    doc = json.loads(data[20:20 + length])
    meshes = {}
    for m in doc.get("meshes", []):
        names = (m.get("extras") or {}).get("targetNames", [])
        meshes[m.get("name", "?")] = {"morph_targets": len(names), "target_names": names}
    joints = []
    for s in doc.get("skins", []):
        joints += [doc["nodes"][j]["name"] for j in s["joints"]]
    return {"bytes": len(data), "meshes": meshes, "joints": len(joints),
            "non_def_joints": [j for j in joints if not j.startswith("DEF-")],
            "materials": [m.get("name") for m in doc.get("materials", [])]}
