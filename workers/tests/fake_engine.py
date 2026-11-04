"""A stand-in for Blender used to test the supervisor's failure handling. The mode is the
job's ``object_name`` param (the only free-text field the smoke job has)."""

import json
import os
import sys
import time
from pathlib import Path

job_path = Path(sys.argv[-1])
ws = job_path.parent
job = json.loads(job_path.read_text())
mode = job["params"]["object_name"]


def result(**kw):
    (ws / "result.json").write_text(json.dumps({"status": "succeeded", "outputs": [], "data": {}, **kw}))


def progress(p, m):
    with open(ws / "progress.jsonl", "a") as f:
        f.write(json.dumps({"progress": p, "message": m}) + "\n")


print("fake engine", mode, flush=True)
if mode == "crash":
    sys.exit(3)
elif mode == "hang":
    progress(0.1, "hanging")
    (ws / "pid").write_text(str(os.getpid()))
    time.sleep(120)
elif mode == "fail":
    result(status="failed", error="RuntimeError: deliberate")
    sys.exit(1)
elif mode == "escape":
    result(outputs=[{"path": "../job.json", "role": "blend", "mime_type": "application/x-blender"}])
elif mode == "envcheck":
    leaked = sorted(k for k in os.environ if k.startswith("STUDIO_") or "SECRET" in k or "KEY" in k)
    result(data={"leaked": leaked, "home": os.environ.get("HOME")})
elif mode == "garbage":
    (ws / "result.json").write_text("{not json")
