"""Phase 5 acceptance check (SRS section 30, Character Assembly), against a running stack.

    docker compose -f infra/compose.yaml up -d
    python scripts/phase5_acceptance.py --out /tmp/phase5

Through the API only, as the creator, reviewer and director users: publish one character
(Ali), two outfits fit-checked and approved on him (school, winter) and three accessories
(glasses, watch, beanie). Then build two scenes from manifests:

* "Two Alis": the same character twice in one scene, in different outfits, with
  accessories, expressions, clips, dialogue and per-instance state;
* "Winter walk": one Ali in the winter outfit with the beanie.

Each scene version is assembled into its own ``scene.blend`` by a Blender job, then reopened
in a fresh workspace by another job and checked against its manifest. Finally the source
assets are checked: same registry rows, same SHA-256s, same bytes in the library bucket, no
new character. Exits non-zero on the first failed criterion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from phase1_acceptance import call, check, login  # noqa: E402
from phase2_acceptance import ALI, wait  # noqa: E402
from phase3_acceptance import build_and_review, publish  # noqa: E402

import phase1_acceptance as p1  # noqa: E402


def piece(slot: str, key: str, **kw) -> dict:
    return {"slot": slot, "garment": {"id": f"GARM-{key.upper()}", "version": "0.1"}, **kw}


OUTFITS = {
    "school": ("School day", [piece("upper", "tshirt", material={"color": "royal_blue", "trim_color": "white"}),
                              piece("lower", "shorts"), piece("footwear", "sneakers", material={"trim_color": "white"})]),
    "winter": ("Winter walk", [piece("upper", "sweater", params={"ease": 0.3}, material={"trim_color": "cream"}),
                               piece("lower", "trousers"), piece("footwear", "boots")]),
}
ACCESSORIES = {
    "glasses": ("Round glasses", {"item": {"id": "ACCD-GLASSES", "version": "0.1"}, "material": {"color": "black"}}),
    "watch": ("Wrist watch", {"item": {"id": "ACCD-WATCH", "version": "0.1"}, "attach_point": "wrist_L"}),
    "beanie": ("Bobble beanie", {"item": {"id": "ACCD-BEANIE", "version": "0.1"}, "material": {"color": "red"}}),
}


def two_alis(c: dict) -> dict:
    return {"name": "Two Alis", "description": "the same character twice, in different outfits",
            "frame_start": 1, "frame_end": 72, "camera": {"preset": "wide", "angle_deg": 15},
            "instances": [
                {"key": "ali_school", "character": c["ali"], "outfit": c["school"],
                 "accessories": [{"accessory": c["glasses"]}, {"accessory": c["watch"], "attach_point": "wrist_R"}],
                 "transform": {"location": [-0.45, 0, 0], "rotation_deg": [0, 0, 15]},
                 "expressions": [{"frame": 1, "expression": "neutral"}, {"frame": 24, "expression": "happy"},
                                 {"frame": 60, "expression": "surprised", "weight": 0.7}],
                 "animations": [{"clip": "wave", "start_frame": 1, "repeat": 2},
                                {"clip": "CLIP-IDLE@0.1", "start_frame": 1}],
                 "dialogue": [{"start_frame": 30, "end_frame": 48, "text": "Hi! Is that me in a beanie?"}],
                 "state": {"mood": "cheerful", "holding": "nothing"}},
                {"key": "ali_winter", "character": c["ali"], "outfit": c["winter"],
                 "accessories": [{"accessory": c["beanie"]}],
                 "transform": {"location": [0.45, 0.1, 0], "rotation_deg": [0, 0, -15]},
                 "expressions": [{"frame": 12, "expression": "confused"}, {"frame": 50, "expression": "laughing"}],
                 "animations": [{"clip": "look_around", "start_frame": 5}, {"clip": "nod", "start_frame": 50}],
                 "dialogue": [{"start_frame": 52, "end_frame": 66, "text": "Yes, it's cold out here."}],
                 "blink": False, "state": {"mood": "puzzled"}}]}


def winter_walk(c: dict) -> dict:
    return {"name": "Winter walk", "frame_start": 1, "frame_end": 48, "camera": {"preset": "medium", "angle_deg": 25},
            "instances": [{"key": "ali", "character": c["ali"], "outfit": c["winter"],
                           "accessories": [{"accessory": c["beanie"]}, {"accessory": c["watch"]}],
                           "expressions": [{"frame": 12, "expression": "excited", "weight": 0.8}],
                           "animations": [{"clip": "walk", "start_frame": 1, "repeat": 2}]}]}


def fetch(url: str, dest: Path | None = None) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as r:
        blob = r.read()
    if dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
    return blob


def asset_file(token: str, kind: str, aid: str, vid: str, name: str, dest: Path | None = None) -> bytes:
    status, dl = call("GET", f"{p1.API}/{kind}/{aid}/versions/{vid}/files/{name}?redirect=false", token)
    if status != 200:
        raise SystemExit(f"download {kind} {name}: {status} {dl}")
    return fetch(dl["url"], dest)


def library_state(token: str, pinned: dict) -> dict:
    """Registry rows and the stored bytes' SHA-256 of every pinned version's library files."""
    state = {}
    for slug, (kind, aid, vid) in pinned.items():
        _, v = call("GET", f"{p1.API}/{kind}/{aid}/versions/{vid}", token)
        rows = {f["name"]: f["sha256"] for f in v["files"]}
        stored = {n: hashlib.sha256(asset_file(token, kind, aid, vid, n)).hexdigest() for n in rows}
        state[slug] = {"status": v["status"], "rows": rows, "stored": stored}
    return state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=p1.API)
    ap.add_argument("--token-url", default=p1.KEYCLOAK)
    ap.add_argument("--out", default="phase5-output")
    ap.add_argument("--resolution", type=int, default=384)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    a = ap.parse_args()
    p1.API, p1.KEYCLOAK = a.api.rstrip("/"), a.token_url
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    previews = {"resolution": a.resolution, "samples": 12, "pose_tile": 256}
    scene_previews = {"width": a.width, "height": a.height, "samples": 16}
    creator, reviewer, director = login("creator"), login("reviewer"), login("director")
    started = time.monotonic()
    pins: dict[str, dict] = {}
    pinned: dict[str, tuple[str, str, str]] = {}

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
    pins["ali"], pinned["ali"] = {"code": c["code"], "version": "1.0"}, ("characters", cid, cvid)
    _, before_chars = call("GET", f"{p1.API}/characters?limit=200", reviewer)

    # ---------------------------------------------------------------- two outfits, approved on Ali
    for slug, (name, pieces) in OUTFITS.items():
        spec = {"fit_profiles": [ali["fit_profile"]], "pieces": pieces}
        status, o = call("POST", f"{p1.API}/outfits", creator,
                         {"name": f"{name} {time.strftime('%H%M%S')}", "tags": [slug], "spec": spec})
        oid, ovid = o["id"], o["versions"][0]["id"]
        base = f"{p1.API}/outfits/{oid}"
        _, g = call("POST", f"{base}/drafts/{ovid}/generate", creator, {"previews": previews})
        job = wait(creator, g["job"]["id"], 1800)
        check(f"{slug}: outfit built and fit-checked on the reference body",
              job["status"] == "succeeded" and job["result"]["fit_ok"], f"{o['code']}")
        _, f = call("POST", f"{base}/versions/{ovid}/fits", creator, {"character_version_id": cvid, "previews": previews})
        job = wait(creator, f["job"]["id"], 1800)
        _, fit = call("GET", f"{base}/versions/{ovid}/fits/{cvid}", creator)
        check(f"{slug}: fit-checked on Ali 1.0", job["status"] == "succeeded" and fit["status"] == "verified",
              ", ".join(fit.get("failed") or []))
        status, d = call("POST", f"{base}/versions/{ovid}/fits/{cvid}/decision", reviewer, {"decision": "approve"})
        call("POST", f"{base}/drafts/{ovid}/submit", creator)
        call("POST", f"{base}/versions/{ovid}/review", reviewer, {"decision": "approve"})
        _, p = call("POST", f"{base}/versions/{ovid}/publish", director, {"bump": "minor"})
        job = wait(director, p["job"]["id"], 1800)
        check(f"{slug}: fit approved and outfit published 1.0",
              d["status"] == "approved" and job["status"] == "succeeded" and job["result"]["version"] == "1.0",
              job["result"]["manifest_key"] if job["status"] == "succeeded" else json.dumps(job.get("error"))[:300])
        pins[slug], pinned[slug] = {"code": o["code"], "version": "1.0"}, ("outfits", oid, ovid)

    # ---------------------------------------------------------------- three accessories
    for slug, (name, spec) in ACCESSORIES.items():
        status, acc = call("POST", f"{p1.API}/accessories", creator,
                           {"name": f"{name} {time.strftime('%H%M%S')}", "tags": [slug], "spec": spec})
        check(f"{slug}: accessory created ({acc.get('category')})", status == 201, f"{acc.get('code')}")
        aid, avid = acc["id"], acc["versions"][0]["id"]
        base = f"{p1.API}/accessories/{aid}"
        _, g = call("POST", f"{base}/drafts/{avid}/generate", creator, {"previews": {"resolution": a.resolution}})
        job = wait(creator, g["job"]["id"], 1800)
        _, v = call("GET", f"{base}/versions/{avid}", creator)
        report = v["check_report"] or {}
        check(f"{slug}: built, attached to the reference body and attach-checked in every pose",
              job["status"] == "succeeded" and job["result"]["check_ok"],
              f"{len(report.get('checks', []))} checks on {v['attach_point']}, points {', '.join(v['attach_points'])}")
        asset_file(creator, "accessories", aid, avid, "on_reference.png", out / "accessories" / f"{slug}-on-reference.png")
        call("POST", f"{base}/drafts/{avid}/submit", creator)
        call("POST", f"{base}/versions/{avid}/review", reviewer, {"decision": "approve"})
        _, p = call("POST", f"{base}/versions/{avid}/publish", director, {"bump": "minor"})
        job = wait(director, p["job"]["id"], 1800)
        check(f"{slug}: publisher re-checked accessory.blend and published 1.0",
              job["status"] == "succeeded" and job["result"]["version"] == "1.0", json.dumps(job.get("error"))[:300])
        pins[slug], pinned[slug] = {"code": acc["code"], "version": "1.0"}, ("accessories", aid, avid)

    before = library_state(creator, pinned)
    (out / "library_before.json").write_text(json.dumps(before, indent=2))

    # ---------------------------------------------------------------- the registry refuses what does not fit
    bad = two_alis(pins)
    bad["instances"][1]["accessories"].append({"accessory": pins["glasses"], "attach_point": "head_top"})
    status, val = call("POST", f"{p1.API}/scenes/validate", creator, {"manifest": bad})
    check("an accessory on a point it does not go on is refused before anything is built",
          status == 200 and not val["ok"] and any(i["code"] == "attach_point" for i in val["issues"]),
          "; ".join(i["message"] for i in val["issues"])[:200])

    # ---------------------------------------------------------------- two scenes
    scenes = {}
    for slug, manifest in (("two_alis", two_alis(pins)), ("winter_walk", winter_walk(pins))):
        status, s = call("POST", f"{p1.API}/scenes", creator,
                         {"name": manifest["name"], "tags": ["phase5"], "manifest": manifest})
        check(f"{slug}: scene created and every pin resolved", status == 201, f"{s.get('code')}")
        sid, svid = s["id"], s["versions"][0]["id"]
        base = f"{p1.API}/scenes/{sid}/versions/{svid}"
        _, v = call("GET", base, creator)
        insts = v["resolved"]["instances"]
        check(f"{slug}: each instance pins exact versions and an approved fit",
              all(i["character"]["version_id"] == cvid and i["outfit_compatibility"]["status"] == "approved"
                  for i in insts),
              "; ".join(f"{i['key']}: {i['character']['code']}@{i['character']['version']} + "
                        f"{i['outfit']['code']}@{i['outfit']['version']} + "
                        f"{', '.join(x['pin']['code'] + ' on ' + x['attach_point'] for x in i['accessories'])}"
                        for i in insts))
        _, job = call("POST", f"{base}/assemble", creator, {"previews": scene_previews})
        job = wait(creator, job["job"]["id"], 2400)
        _, v = call("GET", base, creator)
        rep = v["build_report"] or {}
        check(f"{slug}: Blender assembled scene.blend (assemble_scene job)",
              job["status"] == "succeeded" and v["build_status"] == "built",
              json.dumps(job.get("error"))[:400] if job["status"] != "succeeded" else
              f"{len(rep['instances'])} instance(s), scene.blend {v['scene_blend_sha256'][:16]}")
        acc_checks = [(i["key"], i.get("accessory_check", {})) for i in rep["instances"]]
        check(f"{slug}: every accessory attach-checked on its instance",
              all(ac.get("ok", True) for _, ac in acc_checks),
              "; ".join(f"{k}: {len(ac.get('checks', []))} checks" for k, ac in acc_checks))
        libs = rep["libraries"]["libraries"]
        check(f"{slug}: scene.blend links only the pinned files, by relative path",
              rep["libraries"]["ok"] and all(lb["stored"].startswith("//../assets/") for lb in libs),
              ", ".join(sorted({lb["stored"] for lb in libs})))
        check(f"{slug}: the pinned sources were byte-identical after assembly", rep["sources"]["unchanged"],
              f"{len(rep['sources']['after'])} files hashed before and after")
        for f in v["build_files"]:
            if f["name"] in ("scene.blend", "scene_report.json", "frames.png"):
                fetch(f["url"], out / slug / f["name"])
        scenes[slug] = (sid, svid, v)

    status, s = call("GET", f"{p1.API}/scenes/{scenes['two_alis'][0]}", creator)
    check("the two scene files are separate", scenes["two_alis"][2]["scene_blend_sha256"]
          != scenes["winter_walk"][2]["scene_blend_sha256"], "two scene.blend files")

    # ---------------------------------------------------------------- reopen
    for slug, (sid, svid, v) in scenes.items():
        base = f"{p1.API}/scenes/{sid}/versions/{svid}"
        _, job = call("POST", f"{base}/reopen", creator, {"previews": scene_previews})
        job = wait(creator, job["job"]["id"], 2400)
        _, v = call("GET", base, creator)
        rep = v["verify_report"] or {}
        failed = [c["name"] for c in rep.get("checks", []) if not c["ok"]]
        check(f"{slug}: reopened in a fresh workspace and matched its manifest (verify_scene job)",
              job["status"] == "succeeded" and v["verify_status"] == "verified",
              ", ".join(failed) or f"{len(rep.get('checks', []))} checks, opened from {rep.get('opened_from')}")
        names = {c["name"].split(":", 1)[-1] for c in rep["checks"]}
        check(f"{slug}: overrides, outfits, accessories, placement, face and clips all came back",
              {"character_is_a_library_override", "outfit_bound_to_this_rig", "placement", "expressions_play",
               "animations_assigned", "libraries_resolve", "renders"} <= names, f"{len(names)} kinds of check")
        for f in v["verify_files"]:
            fetch(f["url"], out / slug / f["name"])

    # ---------------------------------------------------------------- the sources are unchanged
    after = library_state(creator, pinned)
    (out / "library_after.json").write_text(json.dumps(after, indent=2))
    for slug in pinned:
        check(f"{slug}: source asset unchanged (registry rows, stored bytes, status)",
              after[slug] == before[slug] and after[slug]["rows"] == after[slug]["stored"],
              f"{len(after[slug]['rows'])} files, {after[slug]['status']}")
    _, after_chars = call("GET", f"{p1.API}/characters?limit=200", reviewer)
    check("no character was created or copied by the scenes", len(after_chars["items"]) == len(before_chars["items"]),
          f"{len(after_chars['items'])} characters")
    _, usage = call("GET", f"{p1.API}/scenes/usage/{cvid}", reviewer)
    check("Ali 1.0 is pinned three times across the two scenes, by reference",
          sorted(u["instance_key"] for u in usage) == ["ali", "ali_school", "ali_winter"],
          ", ".join(f"{u['scene_code']} v{u['number']} {u['instance_key']}" for u in usage))

    summary = {"character": {"id": cid, "code": c["code"], "version_id": cvid}, "pins": pins,
               "scenes": {k: {"scene_id": s, "version_id": v, "scene_blend_sha256": d["scene_blend_sha256"]}
                          for k, (s, v, d) in scenes.items()},
               "elapsed_s": round(time.monotonic() - started)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nPhase 5 acceptance: all criteria passed in {summary['elapsed_s']} s. Files in {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
