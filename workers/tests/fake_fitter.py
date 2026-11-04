"""A stand-in for the Blender side of fit_outfit: checks the staged inputs against the
envelope and reports a passing fit for them."""

import hashlib
import json
import sys
from pathlib import Path

job_path = Path(sys.argv[-1])
ws = job_path.parent
job = json.loads(job_path.read_text())
shas = {}
for i in job["inputs"]:
    sha = hashlib.sha256((ws / i["path"]).read_bytes()).hexdigest()
    assert sha == i["sha256"], i
    shas[Path(i["path"]).name] = sha
report = {"ok": True, "failed": [], "outfit_blend_sha256": shas["outfit.blend"], "fit_profile_declared": True,
          "wearer": {"kind": "character", "fit_profile": "child_average",
                     "character_blend_sha256": shas["character.blend"]}}
(ws / "result.json").write_text(json.dumps({"status": "succeeded", "outputs": [], "data": {
    "ok": True, "report": report, "inputs": sorted(shas)}}))
