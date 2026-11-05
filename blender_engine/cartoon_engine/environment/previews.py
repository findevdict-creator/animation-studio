"""Review renders for props and environments; nothing here is saved into the asset.

* a prop: a 3/4 thumbnail on a neutral floor;
* an environment: the ``wide`` camera mark (16:9) under the set's own lighting, and a top
  plan (orthographic, from above) with each mark drawn as a disc of a character's radius:
  green spawn, blue seat, orange interaction.
"""

from __future__ import annotations

import math
from pathlib import Path

from studio_contracts.environment import CHARACTER_RADIUS_M, ResolvedEnvironment

from cartoon_engine.character import previews as P
from cartoon_engine.character.materials import _principled
from cartoon_engine.environment import lighting

MARK_COLORS = {"spawn": "#3BB54A", "seat": "#2F7DE1", "interaction": "#F08A24"}


def _cycles(scene, samples: int) -> None:
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_adaptive_sampling = True
    try:
        scene.cycles.use_denoising = True
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
    except (TypeError, AttributeError):
        scene.cycles.use_denoising = False
    scene.view_settings.view_transform = "Standard"


def prop_thumbnail(obj, out: Path, opts) -> Path:
    import bpy

    from cartoon_engine.accessory.build import Shape, box
    from cartoon_engine.environment.build import _mesh_object

    scene, cam = P.setup_scene(opts)
    lo, hi = P._bounds([obj])
    s = Shape()
    span = float(max(hi[0] - lo[0], hi[1] - lo[1])) * 4 + 1
    box(s, (0, 0, -0.01), (span, span, 0.02), 0, bevel=0.0)
    floor = _mesh_object("preview_floor", s, [_principled("preview_floor", "#D9D6CF", 0.9, 0.1)])
    scene.collection.objects.link(floor)
    P._frame(cam, lo, hi, 30.0, elevation_deg=22.0)
    P._render_png(scene, out, opts.resolution, opts.resolution)
    bpy.data.objects.remove(floor)
    return out


def wide(env: ResolvedEnvironment, out: Path, resolution: int, samples: int, camera: str = "wide") -> Path:
    import bpy

    scene = bpy.context.scene
    _cycles(scene, samples)
    lighting.apply_world(scene, env.lighting)
    scene.camera = bpy.data.objects[f"CAM_{camera}"]
    P._render_png(scene, out, round(resolution * 16 / 9 / 2) * 2, resolution)
    return out


def plan(env: ResolvedEnvironment, out: Path, resolution: int, samples: int) -> Path:
    """Top view with the marks; the temporary discs and camera are removed afterwards."""
    import bpy

    from cartoon_engine.accessory.build import Shape
    from cartoon_engine.environment.build import _mesh_object
    from cartoon_engine.environment.props import cylinder

    scene = bpy.context.scene
    _cycles(scene, samples)
    lighting.apply_world(scene, env.lighting)
    w, d, h = env.size
    temp = []
    for mk in env.marks:
        s = Shape()
        cylinder(s, (mk.at[0], mk.at[1], mk.at[2] + 0.01), CHARACTER_RADIUS_M, 0.02, 0, n=24)
        # a nose toward the facing
        a = math.radians(mk.facing_deg) - math.pi / 2
        cylinder(s, (mk.at[0] + math.cos(a) * CHARACTER_RADIUS_M, mk.at[1] + math.sin(a) * CHARACTER_RADIUS_M,
                     mk.at[2] + 0.01), 0.05, 0.02, 0, n=8)
        mat = _principled(f"plan_{mk.kind}", MARK_COLORS.get(mk.kind, "#888888"), 0.5, 0.1, emission=0.6)
        o = _mesh_object(f"plan_mark_{mk.name}", s, [mat])
        scene.collection.objects.link(o)
        temp.append(o)
    data = bpy.data.cameras.new("plan_camera")
    data.type = "ORTHO"
    aspect = w / d
    data.ortho_scale = max(w, d) * 1.12 + 0.3
    data.clip_end = 100
    cam = bpy.data.objects.new("plan_camera", data)
    cam.location = (0, 0, max(h, 4.0) + 10)
    scene.collection.objects.link(cam)
    scene.camera = cam
    temp.append(cam)
    rx = resolution if aspect >= 1 else round(resolution * aspect / 2) * 2
    ry = round(resolution / aspect / 2) * 2 if aspect >= 1 else resolution
    P._render_png(scene, out, rx, ry)
    for o in temp:
        bpy.data.objects.remove(o)
    return out


def export_glb(coll, path: Path) -> dict:
    import bpy

    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    meshes = [o for o in coll.all_objects if o.type == "MESH"]
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True, export_apply=False,
                              export_animations=False, export_yup=True, export_materials="EXPORT",
                              export_cameras=False, export_lights=False, export_extras=False)
    return P.inspect_glb(path)
