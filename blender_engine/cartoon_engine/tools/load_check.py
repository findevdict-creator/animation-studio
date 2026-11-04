"""Link several published character files into one fresh scene and validate each (SRS
Phase 3 "load either version independently").

    python -m cartoon_engine.tools.load_check a/character.blend b/character.blend   # bpy wheel
    blender -b --factory-startup --python-exit-code 1 -P load_check.py -- a.blend b.blend

Prints one JSON document: per file, the override collection, the library file it links
from, its stamps and the validation result. Exit code 1 if any file fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def check(paths: list[Path]) -> dict:
    from cartoon_engine.character.library import library_of, link_character
    from cartoon_engine.character.validate import find_parts, validate
    from cartoon_engine.core import blend_io

    blend_io.reset_empty()
    loaded = [(p, link_character(p)) for p in paths]  # all in the scene before any is validated
    out = []
    for path, coll in loaded:
        parts = find_parts(coll)
        stamps = {k: (v if isinstance(v, (int, float, str)) else str(v))
                  for k, v in parts.rig.items() if k.startswith("studio_")}
        report = validate(parts, float(stamps["studio_height_m"]))
        out.append({"file": str(path), "collection": coll.name, "library": library_of(coll), "rig": parts.rig.name,
                    "linked_mesh_data": parts.body.data.library is not None, "stamps": stamps, "ok": report.ok,
                    "failed": report.failed(), "checks": len(report.checks)})
    return {"blender_version": blend_io.blender_version(), "characters": out,
            "ok": all(c["ok"] for c in out)}


def main(argv: list[str]) -> int:
    args = argv[argv.index("--") + 1:] if "--" in argv else argv[1:]
    import bpy  # noqa: F401

    doc = check([Path(a).resolve() for a in args])
    print(json.dumps(doc, indent=2))
    return 0 if doc["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
