"""Phase 3 acceptance check (SRS section 30, Character Library), against a running stack.

    docker compose -f infra/compose.yaml up -d
    python scripts/phase3_acceptance.py --out /tmp/phase3     # needs boto3 and the bpy wheel

Through the API only, as the creator, reviewer and director users: create a character, build
it in Blender, submit, approve and publish version 1.0; try to overwrite 1.0 through the API,
object storage (worker and publisher credentials) and SQL, and check it is unchanged; create
a draft from 1.0, change it, build, approve and publish 2.0; then download both versions'
files through presigned URLs, check every SHA-256 against the manifest, and link both
character.blend files into one fresh Blender scene, validating each. Exits non-zero on the
first failed criterion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from phase1_acceptance import call, check, login  # noqa: E402
from phase2_acceptance import ALI, wait  # noqa: E402

import phase1_acceptance as p1  # noqa: E402

LOAD = r"""
import json, sys
from pathlib import Path
import bpy
from cartoon_engine.tools.load_check import check
print("LOADED " + json.dumps(check([Path(p) for p in sys.argv[1:]])))
"""


def download(token: str, cid: str, vid: str, name: str, dest: Path) -> bytes:
    status, dl = call("GET", f"{p1.API}/characters/{cid}/versions/{vid}/files/{name}?redirect=false", token)
    if status != 200:
        raise SystemExit(f"download {name}: {status} {dl}")
    with urllib.request.urlopen(dl["url"], timeout=120) as r:
        blob = r.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)
    return blob


def build_and_review(creator: str, reviewer: str, cid: str, vid: str, previews: dict) -> dict:
    status, out = call("POST", f"{p1.API}/characters/{cid}/drafts/{vid}/generate", creator, {"previews": previews})
    check("generate job accepted for the draft", status == 202 and out["job"]["params"]["draft_version_id"] == vid,
          out["job"]["id"] if status == 202 else str(out))
    job = wait(creator, out["job"]["id"], 1800)
    check("Blender built and validated the draft", job["status"] == "succeeded", json.dumps(job.get("error"))[:400])
    _, v = call("GET", f"{p1.API}/characters/{cid}/versions/{vid}", creator)
    check("the build is attached to the draft and current", v["ready_for_review"] == [] and len(v["files"]) == 8,
          f"{len(v['files'])} files, built from {v['built_digest'][:12]}")
    status, v = call("POST", f"{p1.API}/characters/{cid}/drafts/{vid}/submit", creator)
    check("draft submitted for review", status == 200 and v["status"] == "review")
    status, v = call("POST", f"{p1.API}/characters/{cid}/versions/{vid}/review", reviewer,
                     {"decision": "approve", "notes": "acceptance"})
    check("reviewer approved it", status == 200 and v["review_decision"] == "approve")
    return v


def publish(director: str, cid: str, vid: str, bump: str) -> dict:
    key = f"phase3-{uuid.uuid4()}"
    req = urllib.request.Request(f"{p1.API}/characters/{cid}/versions/{vid}/publish", method="POST",
                                 data=json.dumps({"bump": bump}).encode(),
                                 headers={"Authorization": f"Bearer {director}", "Content-Type": "application/json",
                                          "Idempotency-Key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    check("publish job accepted on the publisher queue", out["job"]["queue"] == "publish", out["job"]["id"])
    job = wait(director, out["job"]["id"], 1200)
    check("publisher re-validated, copied and recorded the version", job["status"] == "succeeded",
          json.dumps(job.get("error"))[:600])
    return job["result"]


def verify_files(token: str, cid: str, vid: str, label: str, out: Path) -> tuple[dict, dict]:
    _, v = call("GET", f"{p1.API}/characters/{cid}/versions/{vid}", token)
    prefix = f"library/characters/{cid}/versions/{label}/"
    check(f"{label}: every file row points into the library prefix",
          all(f["storage_key"].startswith(prefix) for f in v["files"]), prefix)
    manifest_blob = download(token, cid, vid, "manifest.json", out / label / "manifest.json")
    check(f"{label}: manifest.json matches the recorded SHA-256",
          hashlib.sha256(manifest_blob).hexdigest() == v["manifest_sha256"], v["manifest_sha256"][:16])
    manifest = json.loads(manifest_blob)
    rows = {f["name"]: f for f in v["files"]}
    for entry in manifest["files"]:
        blob = download(token, cid, vid, entry["key"], out / label / entry["key"])
        ok = hashlib.sha256(blob).hexdigest() == entry["sha256"] == rows[entry["key"]]["sha256"]
        check(f"{label}: {entry['key']} SHA-256 matches manifest and registry", ok, f"{len(blob):,} bytes")
    return v, manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=p1.API)
    ap.add_argument("--token-url", default=p1.KEYCLOAK)
    ap.add_argument("--s3", default="http://localhost:8333")
    ap.add_argument("--out", default="phase3-output")
    ap.add_argument("--bpy-python", default=sys.executable, help="a Python with the bpy wheel and cartoon_engine")
    ap.add_argument("--compose-file", default=str(Path(__file__).parents[1] / "infra" / "compose.yaml"))
    ap.add_argument("--resolution", type=int, default=384)
    ap.add_argument("--frames", type=int, default=16)
    a = ap.parse_args()
    p1.API, p1.KEYCLOAK = a.api.rstrip("/"), a.token_url
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    previews = {"resolution": a.resolution, "turntable_frames": a.frames, "samples": 12}
    creator, reviewer, director = login("creator"), login("reviewer"), login("director")
    started = time.monotonic()

    # ---------------------------------------------------------------- 1.0
    status, c = call("POST", f"{p1.API}/characters", creator,
                     {"name": f"Ali {time.strftime('%H%M%S')}", "description": "cheerful eight-year-old",
                      "tags": ["child", "family"], "spec": ALI})
    check("character created with its first draft", status == 201, f"{c.get('code')} {c.get('id')}")
    cid, v1 = c["id"], c["versions"][0]["id"]
    build_and_review(creator, reviewer, cid, v1, previews)
    r1 = publish(director, cid, v1, "minor")
    check("version 1.0 published", r1["version"] == "1.0", r1["manifest_key"])
    one, manifest1 = verify_files(creator, cid, v1, "1.0", out)
    check("1.0 manifest names its rig, fit profile and both validations",
          manifest1["rig_profile"] == "cartoon_humanoid@1.0" and manifest1["validation"]["publish"]["ok"]
          and manifest1["validation"]["build"]["ok"], f"{manifest1['body_fit_profile']}, "
          f"{manifest1['validation']['publish']['checks']} checks at publish")
    blend1 = (out / "1.0" / "character.blend").read_bytes()

    # ---------------------------------------------------------------- overwrite attempts
    status, body = call("PATCH", f"{p1.API}/characters/{cid}/drafts/{v1}", creator, {"spec": ALI})
    check("API refuses to edit published 1.0", status == 409 and body["code"] == "not_a_draft", body["code"])
    status, body = call("POST", f"{p1.API}/characters/{cid}/drafts/{v1}/generate", creator, {})
    check("API refuses to rebuild published 1.0", status == 409, body["code"])
    status, body = call("POST", f"{p1.API}/characters/{cid}/versions/{v1}/publish", director, {"bump": "minor"})
    check("API refuses to publish 1.0 again", status == 409, body["code"])
    status, body = call("DELETE", f"{p1.API}/characters/{cid}/drafts/{v1}", creator)
    check("API refuses to delete published 1.0", status == 409, body["code"])

    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    def s3(identity: str):
        return boto3.client("s3", endpoint_url=a.s3, aws_access_key_id=f"dev-{identity}",
                            aws_secret_access_key=f"dev-{identity}-secret", region_name="us-east-1",
                            config=Config(s3={"addressing_style": "path"}))

    key = f"library/characters/{cid}/versions/1.0/character.blend"
    for identity in ("worker", "api"):
        try:
            s3(identity).put_object(Bucket="studio-library", Key=key, Body=b"tampered")
            check(f"storage refuses a library write with the {identity} identity", False)
        except ClientError as exc:
            check(f"storage refuses a library write with the {identity} identity",
                  exc.response["Error"]["Code"] in ("AccessDenied", "Forbidden"), exc.response["Error"]["Code"])
    try:
        s3("publisher").put_object(Bucket="studio-library", Key=key, Body=b"tampered", IfNoneMatch="*")
        check("storage refuses to overwrite 1.0 even for the publisher", False)
    except ClientError as exc:
        check("storage refuses to overwrite 1.0 even for the publisher (If-None-Match)",
              exc.response["Error"]["Code"] in ("PreconditionFailed", "412"), exc.response["Error"]["Code"])

    sql = (f"UPDATE character_versions SET spec = '{{}}'::jsonb WHERE id = '{v1}'; "
           f"UPDATE asset_files SET sha256 = repeat('0', 64) WHERE version_id = '{v1}';")
    for stmt in sql.split("; "):
        proc = subprocess.run(["docker", "compose", "-f", a.compose_file, "exec", "-T", "postgres", "psql", "-U", "studio",
                               "-d", "studio", "-v", "ON_ERROR_STOP=1", "-c", stmt.rstrip(";")],
                              capture_output=True, text=True, timeout=60)
        msg = (proc.stderr.strip().splitlines() or ["(no error)"])[0]
        check(f"database refuses: {stmt.split(' SET')[0]}", proc.returncode != 0 and "cannot be" in proc.stderr, msg)
    again = download(creator, cid, v1, "character.blend", out / "recheck" / "character.blend")
    check("1.0 character.blend is byte-for-byte unchanged", again == blend1,
          hashlib.sha256(again).hexdigest()[:16])

    # ---------------------------------------------------------------- 2.0
    status, d = call("POST", f"{p1.API}/characters/{cid}/drafts", creator, {"based_on_version_id": v1})
    check("new draft created from published 1.0", status == 201 and d["spec"] == one["spec"]
          and d["based_on_version_id"] == v1, d["id"] if status == 201 else str(d))
    changed = {**d["spec"], "hair": {"style": "spiky", "color": "dark_brown"},
               "body": {**d["spec"]["body"], "height_m": 1.31}}
    status, d = call("PATCH", f"{p1.API}/characters/{cid}/drafts/{d['id']}", creator, {"spec": changed})
    check("the draft's spec was changed (hair, height)", status == 200, json.dumps(d)[:200] if status != 200 else "")
    v2 = d["id"]
    _, again1 = call("GET", f"{p1.API}/characters/{cid}/versions/{v1}", creator)
    check("1.0 is untouched by the new draft", again1["spec"] == one["spec"] and again1["status"] == "published")
    build_and_review(creator, reviewer, cid, v2, previews)
    r2 = publish(director, cid, v2, "major")
    check("version 2.0 published", r2["version"] == "2.0", r2["manifest_key"])
    two, manifest2 = verify_files(creator, cid, v2, "2.0", out)
    check("2.0 manifest records it is based on 1.0", manifest2["based_on"] == {"version_id": v1, "version": "1.0"})

    # ---------------------------------------------------------------- load either version
    paths = [out / "1.0" / "character.blend", out / "2.0" / "character.blend"]
    proc = subprocess.run([a.bpy_python, "-c", LOAD, *map(str, paths)], capture_output=True, text=True, timeout=600)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("LOADED ")), None)
    check("both versions link into one fresh Blender scene", line is not None,
          (proc.stderr or proc.stdout)[-400:] if line is None else "")
    loaded = json.loads(line.removeprefix("LOADED "))
    (out / "load_check.json").write_text(json.dumps(loaded, indent=2))
    for label, ch in zip(("1.0", "2.0"), loaded["characters"]):
        check(f"{label} loads from its own library file and passes every check", ch["ok"] and ch["linked_mesh_data"],
              f"{ch['collection']} / {ch['rig']}, {ch['checks']} checks, height {ch['stamps']['studio_height_m']}")
    a_, b_ = loaded["characters"]
    check("the two loaded versions are different builds",
          a_["stamps"]["studio_spec_digest"] != b_["stamps"]["studio_spec_digest"]
          and a_["library"] != b_["library"])

    # ---------------------------------------------------------------- library views
    _, page = call("GET", f"{p1.API}/characters?q={c['code']}", reviewer)
    item = page["items"][0]
    check("gallery shows the character with its versions",
          item["latest_published"] == "2.0" and item["recommended_version"] == "1.0" and item["thumbnail_url"],
          f"counts {item['counts']}")
    _, history = call("GET", f"{p1.API}/characters/{cid}/versions", reviewer)
    check("version history lists 2.0 then 1.0",
          [(h["version_label"], h["status"]) for h in history] == [("2.0", "published"), ("1.0", "published")])
    summary = {"character_id": cid, "code": c["code"], "versions": {"1.0": v1, "2.0": v2},
               "manifests": {"1.0": r1["manifest_key"], "2.0": r2["manifest_key"]},
               "elapsed_s": round(time.monotonic() - started), "load_check": loaded}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nPhase 3 acceptance: all criteria passed in {summary['elapsed_s']} s. Files in {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
