"""Build a rigged character .blend from a parametric character sheet (asset build phase 1).

    blender -b --python tools/build_character.py -- --library library --characters runs/x/current/characters.json [--only char_mia] [--rescan]
    python -m tools.build_character --library library --characters characters.json      (bpy wheel)

For every character whose ``asset.status`` is ``parametric`` this opens the base file,
applies the proportion sliders and bone-length overrides, scales the figure to
``height_m``, drives the named material slots from the sheet's hex colours, attaches the
hair piece and the outfit's garments, stamps the library custom properties and saves
``library/characters/lib_char_<slug>.blend``. The result scans like any hand-made
character, so the Layout TD and the Blender build never know the difference.

No human step, no network, same output for the same input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tools.blend_common import mesh_bounds, open_blend, scene_prop, script_args, shape_key_names, split_csv

__all__ = ["build_character", "build_all", "hex_to_rgba", "library_id_for", "slider_value", "main"]


# ------------------------------------------------------------- pure helpers
def hex_to_rgba(value: str) -> tuple[float, float, float, float]:
    v = value.lstrip("#")
    r, g, b = (int(v[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (_srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b), 1.0)


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def slider_value(proportion: float) -> float:
    """Sheet proportions are multipliers around 1.0 (0.7 to 1.3); the base's slider shape
    keys are neutral at 0 and run -1..1, so 1.15 -> 0.5 and 0.7 -> -1.0."""
    return max(-1.0, min(1.0, (proportion - 1.0) / 0.3))


def library_id_for(character_id: str) -> str:
    """char_mia -> lib_char_mia"""
    slug = character_id[len("char_") :] if character_id.startswith("char_") else character_id
    return f"lib_char_{slug}"


def parametric_entries(characters: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in characters.get("characters", []) if c.get("asset", {}).get("status") == "parametric"]


# -------------------------------------------------------------- bpy helpers
def _rig():
    import bpy

    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            return obj
    return None


def _meshes():
    import bpy

    return [o for o in bpy.data.objects if o.type == "MESH"]


def _apply_sliders(proportions: dict[str, float], available: list[str]) -> list[str]:
    applied = []
    for obj in _meshes():
        keys = obj.data.shape_keys
        if not keys:
            continue
        for name, value in proportions.items():
            kb = keys.key_blocks.get(name)
            if kb is not None and name in available:
                kb.slider_min = min(kb.slider_min, -1.0)
                kb.value = slider_value(value)
                applied.append(name)
    return sorted(set(applied))


def _apply_bone_overrides(rig, overrides: dict[str, float]) -> list[str]:
    """Scale a bone's length (tail moves along the bone) before anything is parented to it."""
    import bpy

    if rig is None or not overrides:
        return []
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    applied = []
    for name, factor in overrides.items():
        bone = rig.data.edit_bones.get(name)
        if bone is None:
            continue
        bone.tail = bone.head + (bone.tail - bone.head) * factor
        applied.append(name)
    bpy.ops.object.mode_set(mode="OBJECT")
    return applied


def _scale_everything(factor: float) -> None:
    """Uniform scale about the origin applied to the data, so every object keeps scale 1."""
    import bpy

    if abs(factor - 1.0) < 1e-6:
        return
    for obj in _meshes():
        for v in obj.data.vertices:
            v.co *= factor
        if obj.data.shape_keys:
            for kb in obj.data.shape_keys.key_blocks:
                for d in kb.data:
                    d.co *= factor
        obj.location = obj.location * factor
    rig = _rig()
    if rig is not None:
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")
        for bone in rig.data.edit_bones:
            bone.head = bone.head * factor
            bone.tail = bone.tail * factor
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in bpy.data.objects:
        if obj.type == "EMPTY":
            obj.location = obj.location * factor


def _set_material(name: str, base_color: str | None, roughness: float | None, toon: bool) -> None:
    import bpy

    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
        out = mat.node_tree.nodes.get("Material Output") or mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
        mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    if base_color:
        rgba = hex_to_rgba(base_color)
        bsdf.inputs["Base Color"].default_value = rgba
        mat.diffuse_color = rgba
    if roughness is not None:
        bsdf.inputs["Roughness"].default_value = float(roughness)
    if toon:
        bsdf.inputs["Roughness"].default_value = 1.0
        for key in ("Specular IOR Level", "Specular"):
            if key in bsdf.inputs:
                bsdf.inputs[key].default_value = 0.0


def _append_objects(path: Path, into_collection) -> list:
    """Append every mesh object of another .blend into the current file."""
    import bpy

    before = set(bpy.data.objects)
    with bpy.data.libraries.load(str(path), link=False) as (src, dst):
        dst.objects = [n for n in src.objects]
    added = []
    for obj in dst.objects:
        if obj is None or obj.type != "MESH":
            continue
        into_collection.objects.link(obj)
        added.append(obj)
    for obj in set(bpy.data.objects) - before - set(added):
        if obj.type != "MESH":
            bpy.data.objects.remove(obj)
    return added


def _attach(objs, rig, bone: str, scale: float, material_slot: str | None, base_color, roughness, toon) -> None:
    """Scale an appended piece to the figure, parent it to the rig and let the bone carry it."""
    for obj in objs:
        for v in obj.data.vertices:
            v.co *= scale
        obj.location = obj.location * scale
        obj.parent = rig
        if rig is not None:
            group = obj.vertex_groups.get(bone) or obj.vertex_groups.new(name=bone)
            group.add([v.index for v in obj.data.vertices], 1.0, "REPLACE")
            if not any(m.type == "ARMATURE" for m in obj.modifiers):
                mod = obj.modifiers.new("rig", "ARMATURE")
                mod.object = rig
        if material_slot:
            mat_name = f"{material_slot}"
            _set_material(mat_name, base_color, roughness, toon)
            import bpy

            obj.data.materials.clear()
            obj.data.materials.append(bpy.data.materials[mat_name])


def _ref_height(path: Path) -> float:
    """Garments and hair declare the height they were authored for (default 1.0 m)."""
    import bpy

    with bpy.data.libraries.load(str(path), link=False) as (src, dst):
        dst.scenes = [n for n in src.scenes]
    ref = 1.0
    for scene in dst.scenes:
        if scene is not None:
            ref = float(scene.get("studio_ref_height_m", 1.0))
            bpy.data.scenes.remove(scene)
    return ref


# ---------------------------------------------------------------- the build
def build_character(
    library: Path,
    character: dict[str, Any],
    outfit_id: str | None = None,
    toon: bool = False,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Build one parametric character. Returns a record with the new library id and file."""
    import bpy

    asset = character["asset"]
    if asset.get("status") != "parametric":
        raise ValueError(f"{character['id']}: asset.status is {asset.get('status')!r}, not 'parametric'")
    base_id = asset["base_id"]
    base_file = library / "bases" / f"{base_id}.blend"
    if not base_file.exists():
        raise FileNotFoundError(f"{character['id']}: base {base_id} not found at {base_file}")

    open_blend(base_file)
    scene = bpy.context.scene
    rig = _rig()
    sliders = split_csv(scene_prop("studio_proportion_sliders"))
    material_slots = split_csv(scene_prop("studio_material_slots"))
    outfit_slots = split_csv(scene_prop("studio_outfit_slots"))
    rig_type = scene_prop("studio_rig_type", asset.get("rig_type", "custom"))
    record: dict[str, Any] = {"character_id": character["id"], "base_id": base_id, "warnings": []}

    # 1. Proportions: sliders (shape keys) and bone-length overrides.
    record["sliders"] = _apply_sliders(asset.get("proportions") or {}, sliders)
    for name in (asset.get("proportions") or {}):
        if name not in record["sliders"]:
            record["warnings"].append(f"base has no proportion slider {name}")
    record["bone_overrides"] = _apply_bone_overrides(rig, asset.get("proportion_overrides") or {})

    # 2. Height: uniform scale so the figure's bounds match height_m.
    current = mesh_bounds()[2] or 1.0
    target = float(asset["height_m"])
    _scale_everything(target / current)
    record["height_m"] = round(mesh_bounds()[2], 4)

    # 3. Materials on the base's named slots.
    for slot, spec in (asset.get("materials") or {}).items():
        if slot not in material_slots and slot not in bpy.data.materials:
            record["warnings"].append(f"base has no material slot {slot}")
            continue
        _set_material(slot, spec.get("base_color"), spec.get("roughness"), toon)
    record["materials"] = sorted((asset.get("materials") or {}).keys())

    # 4. Hair.
    hair_id = asset.get("hair_id")
    if hair_id:
        hair_file = library / "hair" / f"{hair_id}.blend"
        if hair_file.exists():
            scale = target / _ref_height(hair_file)
            objs = _append_objects(hair_file, scene.collection)
            hair_spec = (asset.get("materials") or {}).get("hair", {})
            _attach(objs, rig, "head", scale, "hair", hair_spec.get("base_color"), hair_spec.get("roughness"), toon)
            for obj in _meshes():
                if obj.name.startswith("hair_cap"):
                    obj.hide_render = obj.hide_viewport = True  # the base's placeholder cap
            record["hair_id"] = hair_id
        else:
            record["warnings"].append(f"hair {hair_id} not found")

    # 5. Outfit: garments per slot, or a tint on a base material named after the slot.
    outfits = {o["id"]: o for o in character.get("outfits", [])}
    chosen = outfit_id or character.get("default_outfit_id")
    outfit = outfits.get(chosen)
    if outfit is None:
        raise ValueError(f"{character['id']}: outfit {chosen} not in the sheet")
    record["outfit_id"] = chosen
    record["garments"] = []
    for slot, spec in (outfit.get("slots") or {}).items():
        if slot not in outfit_slots:
            record["warnings"].append(f"base has no outfit slot {slot}")
        garment_id = spec.get("garment_id")
        if garment_id:
            garment_file = library / "garments" / f"{garment_id}.blend"
            if not garment_file.exists():
                record["warnings"].append(f"garment {garment_id} not found; slot {slot} left plain")
                continue
            scale = target / _ref_height(garment_file)
            objs = _append_objects(garment_file, scene.collection)
            bone = "head" if slot in ("hat",) else "spine"
            _attach(objs, rig, bone, scale, slot, spec.get("base_color"), spec.get("roughness"), toon)
            record["garments"].append(garment_id)
        else:
            _set_material(slot, spec.get("base_color"), spec.get("roughness"), toon)

    # 6. Library identity.
    lib_id = library_id_for(character["id"])
    tags = [character.get("species", ""), character.get("role", ""), base_id] + [t for t in split_csv(scene_prop("studio_tags")) if t]
    scene["studio_kind"] = "character"
    scene["studio_rig_type"] = rig_type
    scene["studio_outfit_slots"] = ",".join(outfit_slots)
    scene["studio_tags"] = ",".join(sorted({t for t in tags if t}))
    scene["studio_built_from"] = base_id
    scene["studio_character_id"] = character["id"]
    scene["studio_outfit_id"] = chosen
    for key in ("studio_proportion_sliders", "studio_material_slots"):
        if key in scene:
            del scene[key]
    # The slider keys stay baked into the file but are not expressions: hide them from the scanner.
    scene["studio_hidden_shape_keys"] = ",".join(sliders)

    out = (out_dir or library / "characters") / f"{lib_id}.blend"
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out), compress=True)
    record.update({"library_id": lib_id, "file": out.relative_to(library).as_posix() if out.is_relative_to(library) else str(out),
                   "rig_type": rig_type, "shape_keys": [k for k in shape_key_names() if k not in sliders]})
    return record


def build_all(library: Path, characters: dict[str, Any], only: list[str] | None = None, toon: bool = False) -> list[dict[str, Any]]:
    records = []
    for entry in parametric_entries(characters):
        if only and entry["id"] not in only:
            continue
        records.append(build_character(library, entry, toon=toon))
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="library")
    parser.add_argument("--characters", required=True, help="characters.json from a stage 1 run")
    parser.add_argument("--only", action="append", default=[], help="character id(s) to build")
    parser.add_argument("--toon", action="store_true", help="flat toon-style materials")
    parser.add_argument("--rescan", action="store_true", help="rewrite <library>/manifest.json afterwards")
    parser.add_argument("--report", default=None, help="write the build records here as JSON")
    args = parser.parse_args(script_args(argv))
    library = Path(args.library).resolve()
    characters = json.loads(Path(args.characters).read_text())
    records = build_all(library, characters, only=args.only or None, toon=args.toon)
    for r in records:
        print(f"built {r['library_id']} from {r['base_id']} ({r['height_m']} m, {len(r['garments'])} garments)")
        for w in r["warnings"]:
            print(f"  warning: {w}")
    if args.report:
        Path(args.report).write_text(json.dumps(records, indent=2) + "\n")
    if args.rescan:
        from tools.scan_library import main as scan_main

        scan_main(["--library", str(library)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
