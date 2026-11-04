"""Link one published character file into a fresh scene and put several published outfits
on it one after another (SRS Phase 4 "attach each independently").

    python -m cartoon_engine.tools.wardrobe_check character.blend character.json a/outfit.blend b/outfit.blend

The character is linked with a library override, so its geometry stays in its own file.
Each outfit is appended, fitted, put through the fit check and detached again; after each
the character must be exactly as before (modifiers, vertex groups, no outfit left over).
Prints one JSON document; exit code 1 if anything fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def check(character_blend: Path, character_json: Path, outfits: list[Path]) -> dict:
    import bpy

    from studio_contracts.character import ResolvedCharacter
    from studio_contracts.outfit import get_conventions

    from cartoon_engine.character.library import library_of, link_character
    from cartoon_engine.character.skeleton import build_skeleton
    from cartoon_engine.character.validate import find_parts
    from cartoon_engine.core import blend_io
    from cartoon_engine.outfit.check import fit_check
    from cartoon_engine.outfit.fit import attach_outfit, detach_outfit, rig_skeleton_mismatch

    rc = ResolvedCharacter.model_validate(json.loads(character_json.read_text())["resolved"])
    conv = get_conventions(rc.style_profile)
    blend_io.reset_empty()
    coll = link_character(character_blend)
    parts = find_parts(coll)
    sk = build_skeleton(rc)
    before = {"modifiers": [m.name for m in parts.body.modifiers], "groups": len(parts.body.vertex_groups)}
    results = []
    for path in outfits:
        fitted = attach_outfit(path, parts, sk, min_clearance=conv.min_clearance_m)
        name = fitted.collection.get("studio_outfit_name")
        report, _ = fit_check(parts, fitted.garments, conv.fit, conv.poses)
        pieces = sorted(g.name for g in fitted.garments)
        hidden = int(fitted.hidden.sum())
        detach_outfit(fitted, parts)
        clean = ([m.name for m in parts.body.modifiers] == before["modifiers"]
                 and len(parts.body.vertex_groups) == before["groups"]
                 and not any(c.name.startswith("OUTFIT_") for c in bpy.data.collections))
        results.append({"file": str(path), "outfit": name, "pieces": pieces, "hidden_body_vertices": hidden,
                        "ok": report.ok, "failed": report.failed(), "checks": len(report.checks),
                        "character_unchanged_after_detach": clean})
    return {"blender_version": blend_io.blender_version(), "character": {
                "file": str(character_blend), "collection": coll.name, "library": library_of(coll),
                "linked_mesh_data": parts.body.data.library is not None,
                "rig_mismatch_m": round(rig_skeleton_mismatch(parts, sk), 5)},
            "outfits": results,
            "ok": all(r["ok"] and r["character_unchanged_after_detach"] for r in results)}


def main(argv: list[str]) -> int:
    args = argv[argv.index("--") + 1:] if "--" in argv else argv[1:]
    import bpy  # noqa: F401

    doc = check(Path(args[0]), Path(args[1]), [Path(p) for p in args[2:]])
    print(json.dumps(doc, indent=2))
    return 0 if doc["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
