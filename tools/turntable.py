"""Render a turntable and contact sheet for one asset or the whole library.

    blender -b --python tools/turntable.py -- --library library [--only lib_prop_lantern] [--frames 12] [--size 512]

Writes library/preview/<id>/f00.png ... and library/preview/<id>.png (contact sheet).
Uses Eevee at low samples; it is a preview, not a render.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from tools.blend_common import kind_from_path, mesh_bounds, open_blend, script_args


def _setup_camera_and_light(bounds):
    import bpy

    size = max(bounds) or 1.0
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("tt_cam")
    cam = bpy.data.objects.new("tt_cam", cam_data)
    scene.collection.objects.link(cam)
    pivot = bpy.data.objects.new("tt_pivot", None)
    scene.collection.objects.link(pivot)
    pivot.location = (0, 0, bounds[2] / 2)
    cam.parent = pivot
    cam.location = (0, -size * 2.6, size * 0.35)
    cam.rotation_euler = (math.radians(82), 0, 0)
    cam_data.lens = 50
    scene.camera = cam
    sun_data = bpy.data.lights.new("tt_sun", "SUN")
    sun_data.energy = 3.0
    sun = bpy.data.objects.new("tt_sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    return pivot


def _set_engine(scene, engine: str) -> None:
    """'eevee' needs a GPU/EGL context; 'cycles' renders on the CPU anywhere (slower)."""
    if engine == "cycles":
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 16
        scene.cycles.use_denoising = False
        return
    for name in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = name
            if hasattr(scene, "eevee"):
                scene.eevee.taa_render_samples = 8
            return
        except TypeError:
            continue
    scene.render.engine = "BLENDER_WORKBENCH"


def render_turntable(file: Path, out_dir: Path, frames: int, size: int, engine: str = "cycles") -> list[Path]:
    import bpy

    open_blend(file)
    scene = bpy.context.scene
    bounds = mesh_bounds()
    pivot = _setup_camera_and_light(bounds)
    _set_engine(scene, engine)
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = True
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(frames):
        pivot.rotation_euler = (0, 0, 2 * math.pi * i / frames)
        path = out_dir / f"f{i:02d}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        paths.append(path)
    return paths


def contact_sheet(frames: list[Path], out: Path, size: int, cols: int = 4) -> None:
    """Stitch frames into one image using Blender's image pixels (no PIL dependency)."""
    import bpy

    try:
        import numpy as np
    except ImportError:
        return
    rows = math.ceil(len(frames) / cols)
    sheet = np.zeros((rows * size, cols * size, 4), dtype=np.float32)
    for i, path in enumerate(frames):
        img = bpy.data.images.load(str(path))
        px = np.array(img.pixels[:], dtype=np.float32).reshape(img.size[1], img.size[0], 4)
        r, c = divmod(i, cols)
        y0 = (rows - 1 - r) * size
        sheet[y0 : y0 + size, c * size : (c + 1) * size] = px[:size, :size]
        bpy.data.images.remove(img)
    result = bpy.data.images.new("contact", cols * size, rows * size, alpha=True)
    result.pixels = sheet.ravel().tolist()
    result.filepath_raw = str(out)
    result.file_format = "PNG"
    result.save()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="library")
    parser.add_argument("--only", default=None, help="asset id")
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--engine", default="cycles", choices=["cycles", "eevee"],
                        help="cycles works on any CPU; eevee is faster but needs a GPU context")
    args = parser.parse_args(script_args(argv))
    library = Path(args.library).resolve()
    preview = library / "preview"
    files = [
        f for f in sorted(library.rglob("*.blend"))
        if "preview" not in f.parts and "source" not in f.parts and kind_from_path(library, f) not in (None, "clip", "generator")
    ]
    if args.only:
        files = [f for f in files if f.stem == args.only]
    for file in files:
        frames = render_turntable(file, preview / file.stem, args.frames, args.size, args.engine)
        contact_sheet(frames, preview / f"{file.stem}.png", args.size)
        print(f"turntable: {file.stem} ({len(frames)} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
