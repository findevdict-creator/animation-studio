# Asset library conventions

`tools/lint_library.py` fails any file that breaks these. `tools/scan_library.py` reads
them to build `manifest.json`; agents only ever see the manifest.

## Layout

```
library/
  characters/   lib_char_<slug>.blend    finished rigged characters
  props/        lib_prop_<slug>.blend
  envs/         lib_env_<slug>.blend     environments (sets)
  clips/baked/  <rig_type>/clip_<slug>.blend   one Action per file, same name as the file
  clips/source/ FBX mocap + LICENCES.md
  bases/        base_<slug>.blend        parametric base meshes with a rig
  garments/     garm_<slug>.blend
  hair/         hair_<slug>.blend
  kits/<kit>/   kit_<kit>_<piece>.blend
  generators/   gen_<slug>.blend         geometry-node groups
  preview/      turntables (generated, not committed)
  manifest.json (generated)
```

File name = asset id. One asset per file.

## Units and axes

Metres, Z up, Y forward, 24 fps, scale 1 on every object. The asset's origin is at ground
level (z = 0) under its centre.

## Custom properties (read by the scanner)

Set on the **scene** of each file. Missing properties fall back to heuristics, but the lint
warns.

| File kind | Property | Meaning |
| --- | --- | --- |
| all | `studio_kind` | `character`, `prop`, `environment`, `clip`, `base`, `garment`, `hair`, `kit_piece`, `generator` |
| all | `studio_tags` | comma-separated tags |
| character, base, clip | `studio_rig_type` | `rigify_human`, `rigify_quadruped`, `rigify_bird`, `custom` |
| character, base | `studio_outfit_slots` | comma-separated slot names |
| base | `studio_proportion_sliders` | comma-separated shape-key names that act as sliders |
| base | `studio_material_slots` | comma-separated material slot names |
| prop | `studio_attach_bone` | bone name on a character rig the prop attaches to |
| environment | `studio_time_variants` | comma-separated variant names (a collection per variant, `time_<variant>`) |
| clip | `studio_loop` | `1` if the action loops |
| clip | `studio_source`, `studio_licence` | provenance |
| garment | `studio_slot`, `studio_fits`, `studio_ref_height_m` | slot name; comma-separated base ids it is weighted to; the base height it was authored at (default 1.0) |
| hair | `studio_fits`, `studio_ref_height_m` | comma-separated base ids; authored height |
| built character | `studio_built_from`, `studio_character_id`, `studio_outfit_id`, `studio_hidden_shape_keys` | written by `tools/build_character.py`: the base, the sheet it came from, the outfit baked in, and the slider keys the scanner must not list as expressions |
| kit_piece | `studio_kit`, `studio_snap_points` | kit id; comma-separated snap point names |
| generator | `studio_generator_kind`, `studio_params` | `terrain`/`scatter`/`prop`; `name:min:max,...` |

## Naming inside a file

- The armature object is named `rig`; its data block too.
- Mesh objects carry shape keys; the scanner lists every shape key except `Basis`.
- Anchors in environments are empties named `anchor_<name>`.
- A clip file contains exactly one Action named like the file; root motion is read from
  the `root` bone's first and last keyframes.
- Lights and cameras are allowed in environments but are ignored by the scanner.

## Bounds and height

`bounds_m` = the axis-aligned size of all mesh geometry in the file at frame 1.
`height_m` of a character = bounds z.

## Parametric bases and built characters

A base (`bases/base_<slug>.blend`) is a rigged figure whose proportion sliders are shape
keys named in `studio_proportion_sliders`, neutral at 0 and running -1..1 (a sheet
proportion of 1.0 maps to 0, 0.7 to -1, 1.3 to 1). Its material slots are materials named
in `studio_material_slots` (`skin`, `hair`, `eyes`; `fur` for animals). Garment and hair
files hold plain meshes authored for `studio_ref_height_m`; `build_character.py` scales
them to the base's height, parents them to the rig and gives them a vertex group named
after the carrying bone (`spine`, `head`), so a weighted garment and a placeholder box go
through the same path. A built character lands in `characters/lib_char_<slug>.blend` and
scans like any other character.
