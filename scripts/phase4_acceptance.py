"""Phase 4 acceptance check (SRS section 30, Outfit Factory), against a running stack.

    docker compose -f infra/compose.yaml up -d
    python scripts/phase4_acceptance.py --out /tmp/phase4     # needs the bpy wheel for the last step

Through the API only, as the creator, reviewer and director users: build and publish one
character (Ali); generate three outfits for him (one owned by him, two for his body-fit
profile), each in Blender on the reference body with a pose-suite fit check; fit-check each
on Ali's published version in its own job; approve the fits; review and publish all three.
Then check that each outfit is its own library asset whose files hold no character, that
Ali's published version is byte-for-byte unchanged and no character was created or copied,
and finally link Ali's library file into one fresh Blender scene and put the three published
outfits on him one after another, each fitted, checked in every pose and detached again.
Exits non-zero on the first failed criterion.
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
from phase2_acceptance import ALI, wait  # noqa: E402
from phase3_acceptance import build_and_review, download, publish  # noqa: E402

import phase1_acceptance as p1  # noqa: E402

WARDROBE = r"""
import json, sys
from pathlib import Path
import bpy
from cartoon_engine.tools.wardrobe_check import check
a = sys.argv[1:]
print("WARDROBE " + json.dumps(check(Path(a[0]), Path(a[1]), [Path(p) for p in a[2:]])))
"""


def piece(slot: str, key: str, **kw) -> dict:
    return {"slot": slot, "garment": {"id": f"GARM-{key.upper()}", "version": "0.1"}, **kw}


OUTFITS = {
    "School day": ("school", [piece("upper", "tshirt", material={"color": "royal_blue", "trim_color": "white"}),
                              piece("lower", "shorts"), piece("footwear", "sneakers", material={"trim_color": "white"}),
                              piece("headwear", "cap"), piece("accessory", "backpack")]),
    "Winter walk": ("winter", [piece("upper", "sweater", params={"ease": 0.3}, material={"trim_color": "cream"}),
                               piece("lower", "trousers"), piece("footwear", "boots")]),
    "Sunday best": ("smart", [piece("upper", "longsleeve", material={"color": "red", "trim_color": "white"}),
                              piece("lower", "trousers", params={"ease": 0.1},
                                    material={"fabric": "cotton", "color": "black"}),
                              piece("footwear", "sneakers", material={"color": "white", "trim_color": "black"})]),
}


def fetch(url: str, dest: Path) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:
        blob = r.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)
    return blob


def outfit_file(token: str, oid: str, vid: str, name: str, dest: Path) -> bytes:
    status, dl = call("GET", f"{p1.API}/outfits/{oid}/versions/{vid}/files/{name}?redirect=false", token)
    if status != 200:
        raise SystemExit(f"download {name}: {status} {dl}")
    return fetch(dl["url"], dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=p1.API)
    ap.add_argument("--token-url", default=p1.KEYCLOAK)
    ap.add_argument("--out", default="phase4-output")
    ap.add_argument("--bpy-python", default=sys.executable, help="a Python with the bpy wheel and cartoon_engine")
    ap.add_argument("--resolution", type=int, default=384)
    a = ap.parse_args()
    p1.API, p1.KEYCLOAK = a.api.rstrip("/"), a.token_url
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    previews = {"resolution": a.resolution, "samples": 12, "pose_tile": 256}
    creator, reviewer, director = login("creator"), login("reviewer"), login("director")
    started = time.monotonic()

    # ---------------------------------------------------------------- the character
    status, c = call("POST", f"{p1.API}/characters", creator,
                     {"name": f"Ali {time.strftime('%H%M%S')}", "description": "cheerful eight-year-old",
                      "tags": ["child", "family"], "spec": ALI})
    check("character created", status == 201, f"{c.get('code')} {c.get('id')}")
    cid, cvid = c["id"], c["versions"][0]["id"]
    build_and_review(creator, reviewer, cid, cvid, {"resolution": a.resolution, "turntable_frames": 8, "samples": 12})
    r = publish(director, cid, cvid, "minor")
    check("Ali 1.0 published", r["version"] == "1.0", r["manifest_key"])
    _, ali = call("GET", f"{p1.API}/characters/{cid}/versions/{cvid}", creator)
    ali_files = {f["name"]: f["sha256"] for f in ali["files"]}
    status, dl = call("GET", f"{p1.API}/characters/{cid}/versions/{cvid}/files/character.blend?redirect=false", creator)
    ali_blend = fetch(dl["url"], out / "ali" / "character.blend")
    download(creator, cid, cvid, "character.json", out / "ali" / "character.json")
    _, before = call("GET", f"{p1.API}/characters?limit=200", reviewer)

    # ---------------------------------------------------------------- three outfits
    made = {}
    for name, (slug, pieces) in OUTFITS.items():
        scope = {"scope": "character", "owner_character": c["code"]} if slug == "school" else {}
        spec = {"fit_profiles": [ali["fit_profile"]], "pieces": pieces, **scope}
        status, o = call("POST", f"{p1.API}/outfits", creator,
                         {"name": f"{name} {time.strftime('%H%M%S')}", "tags": [slug], "spec": spec})
        check(f"{slug}: outfit created ({o.get('scope')})", status == 201, f"{o.get('code')} {o.get('id')}")
        oid, ovid = o["id"], o["versions"][0]["id"]
        base = f"{p1.API}/outfits/{oid}"
        status, g = call("POST", f"{base}/drafts/{ovid}/generate", creator, {"previews": previews})
        job = wait(creator, g["job"]["id"], 1800)
        check(f"{slug}: Blender built the garments and fit-checked them on the reference body",
              job["status"] == "succeeded" and job["result"]["fit_ok"],
              json.dumps(job.get("error") or job["result"]["data"]["report"]["failed"])[:400])
        _, v = call("GET", f"{base}/versions/{ovid}", creator)
        report = v["fit_report"]
        check(f"{slug}: outfit.blend holds the outfit alone (no armature, no character)",
              report["file"]["armatures"] == [] and len(report["file"]["collections"]) == 1,
              f"{report['file']['collections'][0]}, {report['file']['meshes']} meshes")
        outfit_file(creator, oid, ovid, "poses.png", out / slug / "poses-reference.png")

        # attach to Ali, in its own Blender job, from the exact library and draft files
        status, f = call("POST", f"{base}/versions/{ovid}/fits", creator,
                         {"character_version_id": cvid, "previews": previews})
        check(f"{slug}: fit check on Ali requested", status == 202, f["job"]["id"] if status == 202 else str(f))
        job = wait(creator, f["job"]["id"], 1800)
        check(f"{slug}: fit job ran", job["status"] == "succeeded", json.dumps(job.get("error"))[:400])
        _, fit = call("GET", f"{base}/versions/{ovid}/fits/{cvid}", creator)
        rep = fit["report"]
        check(f"{slug}: attached to Ali's published character.blend",
              rep["wearer"]["character_blend_sha256"] == ali_files["character.blend"]
              and rep["wearer"]["rig_mismatch_m"] < 0.002, f"rig within {rep['wearer']['rig_mismatch_m'] * 1000:.2f} mm")
        deform = [ch for ch in rep["checks"] if ch["name"].split(":")[0] in ("no_clipping", "deformation", "stays_on_body")]
        check(f"{slug}: basic deformation holds in every pose (clipping, stretch, lift)",
              fit["status"] == "verified" and all(ch["ok"] for ch in deform),
              f"{len(deform)} garment checks over {len(rep['pose_suite'])} poses" if fit["status"] == "verified"
              else ", ".join(fit["failed"]))
        for fl in fit["files"]:
            fetch(fl["url"], out / slug / f"on-ali-{fl['name']}")
        status, d = call("POST", f"{base}/versions/{ovid}/fits/{cvid}/decision", reviewer, {"decision": "approve"})
        check(f"{slug}: reviewer approved the fit", status == 200 and d["status"] == "approved")

        status, v = call("POST", f"{base}/drafts/{ovid}/submit", creator)
        check(f"{slug}: submitted for review", status == 200 and v["status"] == "review", str(v.get("problems", "")))
        status, v = call("POST", f"{base}/versions/{ovid}/review", reviewer, {"decision": "approve"})
        check(f"{slug}: reviewer approved the outfit", status == 200 and v["review_decision"] == "approve")
        status, p = call("POST", f"{base}/versions/{ovid}/publish", director, {"bump": "minor"})
        job = wait(director, p["job"]["id"], 1800)
        check(f"{slug}: publisher re-checked outfit.blend in a fresh process and published 1.0",
              job["status"] == "succeeded" and job["result"]["version"] == "1.0", json.dumps(job.get("error"))[:400])
        made[slug] = (oid, ovid, job["result"])

    # ---------------------------------------------------------------- published, separately, no duplication
    for slug, (oid, ovid, result) in made.items():
        _, v = call("GET", f"{p1.API}/outfits/{oid}/versions/{ovid}", creator)
        prefix = f"library/outfits/{oid}/versions/1.0/"
        manifest_blob = outfit_file(creator, oid, ovid, "manifest.json", out / slug / "manifest.json")
        manifest = json.loads(manifest_blob)
        rows = {f["name"]: f for f in v["files"]}
        ok = hashlib.sha256(manifest_blob).hexdigest() == v["manifest_sha256"] and all(
            f["storage_key"].startswith(prefix) for f in v["files"])
        for entry in manifest["files"]:
            blob = outfit_file(creator, oid, ovid, entry["key"], out / slug / entry["key"])
            ok = ok and hashlib.sha256(blob).hexdigest() == entry["sha256"] == rows[entry["key"]]["sha256"]
        check(f"{slug}: its own library prefix, every SHA-256 matches manifest and registry", ok,
              f"{len(manifest['files'])} files under {prefix}")
        check(f"{slug}: the published files hold no character",
              not any(e["key"].startswith("character") for e in manifest["files"]) and manifest["kind"] == "outfit",
              ", ".join(sorted(e["key"] for e in manifest["files"])))
        check(f"{slug}: the manifest names its wearer by reference",
              [w["character_version_id"] for w in manifest["verified_fits"]] == [cvid]
              and (manifest["owner_character"] or {}).get("code", c["code"]) == c["code"],
              f"scope {manifest['scope']}, fits {[(w['code'], w['version'], w['status']) for w in manifest['verified_fits']]}")
    _, after = call("GET", f"{p1.API}/characters?limit=200", reviewer)
    check("no character was created or copied by the outfits", len(after["items"]) == len(before["items"]),
          f"{len(after['items'])} characters")
    _, again = call("GET", f"{p1.API}/characters/{cid}/versions/{cvid}", creator)
    status, dl = call("GET", f"{p1.API}/characters/{cid}/versions/{cvid}/files/character.blend?redirect=false", creator)
    blob = fetch(dl["url"], out / "ali" / "recheck.blend")
    check("Ali 1.0 is untouched (files, rows, character.blend bytes)",
          {f["name"]: f["sha256"] for f in again["files"]} == ali_files and blob == ali_blend
          and again["status"] == "published", hashlib.sha256(blob).hexdigest()[:16])
    _, wardrobe = call("GET", f"{p1.API}/characters/{cid}/versions/{cvid}/outfits?status=approved", reviewer)
    check("Ali's wardrobe lists the three approved outfits",
          {w["outfit_version_id"] for w in wardrobe} == {ovid for _, ovid, _ in made.values()},
          ", ".join(w["outfit"]["code"] for w in wardrobe))

    # ---------------------------------------------------------------- dress the library character
    paths = [out / "ali" / "character.blend", out / "ali" / "character.json"] + [out / s / "outfit.blend" for s in made]
    proc = subprocess.run([a.bpy_python, "-c", WARDROBE, *map(str, paths)], capture_output=True, text=True, timeout=900)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("WARDROBE ")), None)
    check("Ali's library file links into a fresh Blender scene", line is not None,
          (proc.stderr or proc.stdout)[-400:] if line is None else "")
    doc = json.loads(line.removeprefix("WARDROBE "))
    (out / "wardrobe_check.json").write_text(json.dumps(doc, indent=2))
    check("the linked character keeps its geometry in its own file", doc["character"]["linked_mesh_data"],
          doc["character"]["library"])
    for slug, res in zip(made, doc["outfits"]):
        check(f"{slug}: attached alone to the linked character, every pose passes, detached cleanly",
              res["ok"] and res["character_unchanged_after_detach"],
              f"{len(res['pieces'])} pieces, {res['checks']} checks, {res['hidden_body_vertices']} body vertices masked")

    summary = {"character": {"id": cid, "code": c["code"], "version_id": cvid},
               "outfits": {s: {"outfit_id": o, "version_id": v, "manifest_key": r["manifest_key"]}
                           for s, (o, v, r) in made.items()},
               "elapsed_s": round(time.monotonic() - started), "wardrobe_check": doc}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nPhase 4 acceptance: all criteria passed in {summary['elapsed_s']} s. Files in {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
