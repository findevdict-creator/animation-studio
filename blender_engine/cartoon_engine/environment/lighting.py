"""Lighting rigs from a preset (``LIGHTING`` in the environment contract).

A rig is a sun (or moon), a soft fill from the camera side and, indoors, a grid of ceiling
area lights; the sky is the world colour. The rig lives in its own ``LIGHTS_*`` collection of
the environment file, so a scene links it with the set, or, when the scene overrides the
lighting, builds the override with this same code instead. Worlds cannot live in a
collection, so the sky is applied to the scene by ``apply_world`` from the preset.
"""

from __future__ import annotations

import math

from studio_contracts.environment import LightingPreset

from cartoon_engine.character.materials import hex_to_linear


def _rgb(hex_: str) -> tuple[float, float, float]:
    return hex_to_linear(hex_)[:3]


def build_rig(preset: LightingPreset, size: tuple[float, float, float], kind: str, name: str):
    """A ``LIGHTS_<name>`` collection holding the preset's lights for a set of ``size``."""
    import bpy

    coll = bpy.data.collections.new(f"LIGHTS_{name}")
    coll["studio_lighting"] = preset.key

    def add(obj_name, kind_, energy, color, loc=(0, 0, 0), rot=(0, 0, 0), **kw):
        data = bpy.data.lights.new(obj_name, kind_)
        data.energy = energy
        data.color = _rgb(color)
        for k, v in kw.items():
            setattr(data, k, v)
        obj = bpy.data.objects.new(obj_name, data)
        obj.location = loc
        obj.rotation_euler = rot
        coll.objects.link(obj)
        return obj

    # a sun at elevation e and heading h shines down toward the heading's opposite
    el, hd = math.radians(preset.sun_elevation_deg), math.radians(preset.sun_heading_deg)
    add(f"{name}_sun", "SUN", preset.sun_energy, preset.sun_color, loc=(0, 0, 6),
        rot=(math.pi / 2 - el, 0, hd), angle=math.radians(3.0))
    w, d, h = size
    # fill: a large soft area light south of the set (camera side), facing north-down
    fill_h = (h if kind == "room" else 3.0) * 0.8
    add(f"{name}_fill", "AREA", preset.sun_energy * preset.fill_share * 25 * max(w, d), preset.sky_color,
        loc=(0, -d / 2 - 1.5, fill_h), rot=(math.radians(70), 0, 0), size=max(w, 3.0), shape="SQUARE")
    if kind == "room" and preset.ceiling_energy_w > 0:
        nx, ny = max(1, math.ceil(w / 3.0)), max(1, math.ceil(d / 3.0))
        for i in range(nx):
            for j in range(ny):
                x, y = -w / 2 + w * (i + 0.5) / nx, -d / 2 + d * (j + 0.5) / ny
                add(f"{name}_ceiling_{i}_{j}", "AREA", preset.ceiling_energy_w, preset.ceiling_color,
                    loc=(x, y, h - 0.05), size=0.8, shape="DISK")
    return coll


def apply_world(scene, preset: LightingPreset) -> None:
    import bpy

    world = bpy.data.worlds.new(f"sky_{preset.key}")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (*_rgb(preset.sky_color), 1.0)
    bg.inputs[1].default_value = preset.sky_strength
    world["studio_lighting"] = preset.key
