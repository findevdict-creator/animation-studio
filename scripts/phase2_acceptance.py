"""Phase 2 acceptance check (SRS section 30, Character Factory), against a running stack.

    docker compose -f infra/compose.yaml up -d
    python scripts/phase2_acceptance.py --out /tmp/phase2      # needs the bpy wheel for the reopen check
    python scripts/phase2_acceptance.py --ai "Ali is a cheerful eight-year-old ..."   # with a provider key

Logs in through Keycloak, validates a character specification through the API (or has the
AI interpret a description first), submits ``generate_character``, waits for the Blender
worker, downloads every file through presigned URLs and checks its SHA-256, then reopens the
downloaded .blend in a fresh background Blender process and runs the character validator
(rig standard, weights, FK arm, IK leg, head turn, every facial control). Exits non-zero on
the first failed criterion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from phase1_acceptance import call, check, login  # noqa: E402

import phase1_acceptance as p1  # noqa: E402

ALI = {
    "name": "Ali",
    "body": {"age_appearance": "child", "body_type": "average", "height_m": 1.28,
             "proportions": {"leg_length": 0.1, "belly": -0.2}},
    "face": {"shape": "rounded", "eye_shape": "large_rounded", "eye_size": 0.7, "eye_color": "brown",
             "nose": "small", "mouth": "friendly", "sliders": {"cheek_fullness": 0.3}},
    "hair": {"style": "short", "color": "black"},
    "materials": {"skin": {"preset": "warm_medium"}},
}

REOPEN = r"""
import json, sys
from pathlib import Path
import bpy
from cartoon_engine.core import blend_io
from cartoon_engine.character.validate import find_parts, validate
blend_io.open_file(Path(sys.argv[1]))
parts = find_parts()
r = validate(parts, float(parts.rig["studio_height_m"]))
print("REOPEN " + json.dumps({"blender": bpy.app.version_string, **r.as_dict()}))
"""


def wait(token: str, job_id: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        _, job = call("GET", f"{p1.API}/jobs/{job_id}", token)
        msg = f"{job['status']} {job['progress']:.0%} {job['progress_message']}"
        if msg != last:
            print(f"      {msg}")
            last = msg
        if job["status"] in ("succeeded", "failed", "timed_out", "cancelled"):
            return job
        time.sleep(2)
    raise SystemExit(f"job {job_id} did not finish in {timeout_s:.0f} s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=p1.API)
    ap.add_argument("--token-url", default=p1.KEYCLOAK)
    ap.add_argument("--out", default="phase2-output")
    ap.add_argument("--ai", metavar="DESCRIPTION", help="have the AI interpret this description first")
    ap.add_argument("--bpy-python", default=sys.executable, help="a Python with the bpy wheel and cartoon_engine")
    ap.add_argument("--resolution", type=int, default=512)
    ap.add_argument("--frames", type=int, default=24)
    a = ap.parse_args()
    p1.API, p1.KEYCLOAK = a.api.rstrip("/"), a.token_url
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    creator = login("creator")
    spec = ALI
    if a.ai:
        status, job = call("POST", f"{p1.API}/jobs", creator, {"type": "interpret_character",
                                                                "params": {"description": a.ai}})
        check("AI interpretation job accepted", status == 202, job.get("id", job))
        job = wait(creator, job["id"], 300)
        check("AI returned a valid specification", job["status"] == "succeeded",
              json.dumps(job.get("error"))[:300])
        spec = job["result"]["data"]["spec"]
        print(f"      unrepresented: {job['result']['data']['unrepresented']}")
    status, v = call("POST", f"{p1.API}/characters/spec/validate", creator, spec)
    check("specification validates against the style profile", status == 200 and v["valid"],
          f"fit profile {v.get('resolved', {}).get('fit_profile')}, digest {str(v.get('digest'))[:12]}")
    status, bad = call("POST", f"{p1.API}/characters/spec/validate", creator, {**spec, "hair": {"style": "mohawk"}})
    check("an unsupported value is rejected with its path", not bad["valid"] and bad["issues"][0]["path"] == "hair.style")
    status, body = call("POST", f"{p1.API}/jobs", creator,
                        {"type": "generate_character", "params": {"spec": {**spec, "overrides": {"x": 1}}}})
    check("the job API refuses an invalid specification", status == 422, body.get("code", ""))

    started = time.monotonic()
    status, job = call("POST", f"{p1.API}/jobs", creator, {
        "type": "generate_character",
        "params": {"spec": spec, "previews": {"resolution": a.resolution, "turntable_frames": a.frames}}})
    check("generate_character job accepted", status == 202, job.get("id", job))
    job = wait(creator, job["id"], 1800)
    check("Blender generated, saved, reopened and validated the character", job["status"] == "succeeded",
          json.dumps(job.get("error"))[:500])
    data = job["result"]["data"]
    print(f"      {time.monotonic() - started:.0f} s on {job['worker_host']} with Blender {job['blender_version']}; "
          f"timings {data['timings_s']}")
    check("the in-job validator passed every check", data["validation"]["ok"], f"{data['validation']['checks']} checks")

    _, version = call("GET", f"{p1.API}/assets/versions/{job['result']['asset_version_id']}", creator)
    check("a character draft was recorded", version["kind"] == "character" and version["status"] == "draft",
          version["display_code"])
    files = {}
    for f in version["files"]:
        _, dl = call("GET", f"{p1.API}/assets/files/{f['id']}/download", creator)
        name = f["storage_key"].rsplit("/", 1)[-1]
        with urllib.request.urlopen(dl["url"], timeout=60) as r:
            blob = r.read()
        (out / name).write_bytes(blob)
        ok = hashlib.sha256(blob).hexdigest() == f["sha256"] and len(blob) == f["size_bytes"]
        check(f"downloaded {name} matches its SHA-256", ok, f"{len(blob):,} bytes")
        files[name] = f
    report = json.loads((out / "validation.json").read_text())
    check("validation.json says the saved file is valid", report["ok"], ", ".join(c["name"] for c in report["checks"][:6]) + ", ...")
    check("turntable MP4 rendered", (out / "turntable.mp4").read_bytes()[4:8] == b"ftyp",
          f"{data['turntable_frames']} frames")
    check("expression sheet rendered", (out / "expressions.png").exists(), ", ".join(data["expression_sheet"]))
    check("browser GLB exported with facial morph targets", data["glb"]["head_morph_targets"] == 43,
          f"{data['glb']['joints']} DEF joints")

    proc = subprocess.run([a.bpy_python, "-c", REOPEN, str(out / "character.blend")], capture_output=True, text=True,
                          timeout=300)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("REOPEN ")), None)
    check("the .blend reopens in a fresh background Blender process", line is not None,
          (proc.stderr or proc.stdout)[-300:] if line is None else "")
    reopened = json.loads(line.removeprefix("REOPEN "))
    motion = [c for c in reopened["checks"] if c["name"] in (
        "fk_arm_moves_hand_only", "ik_leg_lifts_foot", "head_turn", "face_controls_move_face",
        "visemes_override_mouth", "eyes_track_target")]
    for c in motion:
        check(f"reopened file: {c['name']}", c["ok"], json.dumps({k: v for k, v in c.items() if k not in ('name', 'ok')}))
    check("reopened file passes every validator check", reopened["ok"], f"Blender {reopened['blender']}")
    print(f"\nPhase 2 acceptance: all criteria passed. Files in {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
