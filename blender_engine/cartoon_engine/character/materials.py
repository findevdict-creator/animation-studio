"""Materials from the style profile. Principled BSDF only, so Cycles (headless, CPU) and
Eevee (with a GPU) render the same file; the matte toon look comes from roughness and low
specular, not from a shader that only one engine has."""

from __future__ import annotations

from studio_contracts.character import ResolvedCharacter

#: material name -> role; the names are part of the template contract (outfits and
#: validators look them up)
SLOTS = ("skin", "hair", "brow", "eye_white", "iris", "pupil", "mouth", "teeth", "base_suit")


def hex_to_linear(value: str) -> tuple[float, float, float, float]:
    v = value.lstrip("#")
    rgb = [int(v[i: i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return (lin[0], lin[1], lin[2], 1.0)


def _principled(name: str, color: str, roughness: float, specular: float, subsurface: float = 0.0,
                emission: float = 0.0):
    import bpy

    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if mat.node_tree is None:  # new materials have nodes in Blender 5; use_nodes goes away in 6.0
        mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF") or nodes.new("ShaderNodeBsdfPrincipled")
    rgba = hex_to_linear(color)
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Specular IOR Level"].default_value = specular
    if subsurface:
        bsdf.inputs["Subsurface Weight"].default_value = subsurface
        bsdf.inputs["Subsurface Radius"].default_value = (0.6, 0.25, 0.15)
        bsdf.inputs["Subsurface Scale"].default_value = 0.01
    if emission:
        bsdf.inputs["Emission Color"].default_value = rgba
        bsdf.inputs["Emission Strength"].default_value = emission
    mat.diffuse_color = rgba  # solid-mode viewport colour
    mat.roughness = roughness
    mat["studio_slot"] = name
    return mat


def build_materials(rc: ResolvedCharacter) -> dict:
    return {
        "skin": _principled("skin", rc.skin_color, rc.skin_roughness, rc.skin_specular, rc.skin_subsurface),
        "hair": _principled("hair", rc.hair_color, rc.hair_roughness, 0.35),
        "brow": _principled("brow", rc.hair_color, 0.7, 0.1),
        "eye_white": _principled("eye_white", "#F7F5F0", rc.eye_roughness, 0.5),
        "iris": _principled("iris", rc.eye_color, rc.eye_roughness, 0.5),
        "pupil": _principled("pupil", "#0A0708", 0.1, 0.6),
        "mouth": _principled("mouth", "#5A1E22", 0.6, 0.2),
        "teeth": _principled("teeth", "#F4F1EA", 0.35, 0.3),
        # Neutral underlayer on the torso and hips until outfits arrive (Phase 4); outfits hide it.
        "base_suit": _principled("base_suit", "#8C9BB0", 0.8, 0.1),
    }
