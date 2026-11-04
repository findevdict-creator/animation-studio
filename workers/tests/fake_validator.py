"""A stand-in for the Blender side of publish_character. Its "character.blend" is JSON naming
the spec digest it was built from, and optionally a mode ("invalid", "crash")."""

import hashlib
import json
import sys
from pathlib import Path

job_path = Path(sys.argv[-1])
ws = job_path.parent
job = json.loads(job_path.read_text())
(inp,) = job["inputs"]
blend = ws / inp["path"]
sha = hashlib.sha256(blend.read_bytes()).hexdigest()
doc = json.loads(blend.read_text())
mode = doc.get("mode", "ok")
if mode == "crash":
    sys.exit(3)
report = {"ok": mode != "invalid", "failed": ["fake_check"] if mode == "invalid" else [],
          "checks": [{"name": "fake_check", "ok": mode != "invalid"}], "blend_sha256": sha,
          "blender_version": "fake 0.0", "engine_version": "fake", "purpose": "publish"}
(ws / "out").mkdir(exist_ok=True)
(ws / "out" / "publish_validation.json").write_text(json.dumps(report, indent=2))
(ws / "result.json").write_text(json.dumps({"status": "succeeded", "outputs": [], "data": {
    "report": report, "stamps": {"studio_spec_digest": doc["digest"], "studio_rig_profile": "cartoon_humanoid@1.0"}}}))
