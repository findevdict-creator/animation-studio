"""Phase 7 acceptance check (SRS section 30, Environments and Scenes), against a running stack.

    docker compose -f infra/compose.yaml up -d
    python scripts/phase7_acceptance.py --out /tmp/phase7

Through the API only, as the creator, reviewer and director users:

* publish two characters, Ali and Maya, and an outfit for each, fit-checked and approved on
  the character that wears it;
* build, review and publish four props (a ball, a stack of books, an apple, a toy car):
  Blender builds each one, measures it against the size the contract computed and renders it;
* show that an environment whose dressing overlaps is refused before anything is built;
* build, review and publish a living room with two named marks: Blender builds the shell,
  dressing, lighting rig and camera marks, measures the layout again (every item inside,
  grounded, clear of the others, every mark and the door reachable) and renders it;
* show that a scene with a prop inside the sofa, an apple on the floor and a character at a
  mark the room does not have is refused before anything is built, with every issue listed;
* assemble a scene with both characters in their outfits at the room's marks, the props on
  the floor and on the room's table, an evening lighting override and the room's wide camera
  mark, and render preview frames and a preview video;
* reopen the scene in a fresh workspace and check it: the environment and every prop came
  from their pinned files, props stand where the manifest put them (on the floor or on the
  table top), nothing overlaps, and each character's path stays clear of the furniture;
* check that every published asset's files are unchanged.

Exits non-zero on the first failed criterion.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from phase1_acceptance import call, check, login  # noqa: E402
from phase2_acceptance import ALI, wait  # noqa: E402
from phase3_acceptance import build_and_review, publish  # noqa: E402
from phase5_acceptance import asset_file, fetch, library_state, piece  # noqa: E402

import phase1_acceptance as p1  # noqa: E402

MAYA = {
    "name": "Maya",
    "body": {"age_appearance": "child", "body_type": "slim", "height_m": 1.24},
    "face": {"shape": "heart", "eye_shape": "almond", "eye_size": 0.6, "eye_color": "green", "nose": "button",
             "mouth": "small"},
    "hair": {"style": "bob", "color": "auburn"},
    "materials": {"skin": {"preset": "light"}},
}
OUTFITS = {
    "ali": ("School day", [piece("upper", "tshirt", material={"color": "royal_blue", "trim_color": "white"}),
                           piece("lower", "shorts"), piece("footwear", "sneakers", material={"trim_color": "white"})]),
    "maya": ("Winter walk", [piece("upper", "sweater", params={"ease": 0.3}, material={"trim_color": "cream"}),
                             piece("lower", "trousers"), piece("footwear", "boots")]),
}
PROPS = {
    "ball": ("Red ball", {"item": {"id": "PROPD-BALL", "version": "0.1"}, "material": {"color": "red"}}),
    "books": ("Stack of books", {"item": {"id": "PROPD-BOOKS", "version": "0.1"}}),
    "apple": ("Apple", {"item": {"id": "PROPD-APPLE", "version": "0.1"}}),
    "car": ("Toy car", {"item": {"id": "PROPD-TOY_CAR", "version": "0.1"}, "material": {"color": "royal_blue"}}),
}
ROOM = {"template": {"id": "ENVD-LIVING_ROOM", "version": "0.1"},
        "marks": [{"name": "rug", "at": [-0.6, -0.8], "facing_deg": -20},
                  {"name": "window", "at": [1.2, -0.3], "facing_deg": 30},
                  {"name": "sofa_seat", "kind": "seat", "item": "sofa_1"}]}
PREVIEW_FRAMES = [1, 16, 32, 48]


def set_asset(creator, reviewer, director, plural: str, slug: str, name: str, spec: dict, res: int, out: Path):
    """Create, generate, review and publish a prop or environment; returns (owner, version, detail)."""
    status, o = call("POST", f"{p1.API}/{plural}", creator,
                     {"name": f"{name} {time.strftime('%H%M%S')}", "tags": ["phase7", slug], "spec": {"name": name, **spec}})
    check(f"{slug}: {plural[:-1]} draft created", status == 201, f"{o.get('code')}" if status == 201 else json.dumps(o)[:300])
    oid, vid = o["id"], o["versions"][0]["id"]
    base = f"{p1.API}/{plural}/{oid}"
    _, g = call("POST", f"{base}/drafts/{vid}/generate", creator, {"previews": {"resolution": res, "samples": 16}})
    job = wait(creator, g["job"]["id"], 1800)
    _, v = call("GET", f"{base}/versions/{vid}", creator)
    rep = v["check_report"] or {}
    failed = [c["name"] for c in rep.get("checks", []) if not c["ok"]]
    check(f"{slug}: Blender built it and its checks passed",
          job["status"] == "succeeded" and job["result"]["check_ok"],
          json.dumps(job.get("error"))[:400] if job["status"] != "succeeded" else
          f"{len(rep.get('checks', []))} checks, failed: {failed or 'none'}")
    for f in v["files"]:
        if f["name"].endswith((".png", ".json")):
            asset_file(creator, plural, oid, vid, f["name"], out / plural / f"{slug}-{f['name']}")
    call("POST", f"{base}/drafts/{vid}/submit", creator)
    call("POST", f"{base}/versions/{vid}/review", reviewer, {"decision": "approve", "notes": "acceptance"})
    _, p = call("POST", f"{base}/versions/{vid}/publish", director, {"bump": "minor"})
    job = wait(director, p["job"]["id"], 1800)
    check(f"{slug}: publisher re-checked the .blend and published 1.0",
          job["status"] == "succeeded" and job["result"]["version"] == "1.0", json.dumps(job.get("error"))[:300])
    _, v = call("GET", f"{base}/versions/{vid}", creator)
    return o, vid, v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=p1.API)
    ap.add_argument("--token-url", default=p1.KEYCLOAK)
    ap.add_argument("--out", default="phase7-output")
    ap.add_argument("--resolution", type=int, default=384)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--video-every", type=int, default=2)
    a = ap.parse_args()
    p1.API, p1.KEYCLOAK = a.api.rstrip("/"), a.token_url
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    previews = {"resolution": a.resolution, "samples": 12, "pose_tile": 256}
    creator, reviewer, director = login("creator"), login("reviewer"), login("director")
    started = time.monotonic()
    pins: dict[str, dict] = {}
    pinned: dict[str, tuple[str, str, str]] = {}

    # ---------------------------------------------------------------- two characters, an outfit each
    for slug, spec in (("ali", ALI), ("maya", MAYA)):
        status, c = call("POST", f"{p1.API}/characters", creator,
                         {"name": f"{spec['name']} {time.strftime('%H%M%S')}", "tags": ["phase7"], "spec": spec})
        check(f"{slug}: character created", status == 201, f"{c.get('code')}")
        cid, cvid = c["id"], c["versions"][0]["id"]
        build_and_review(creator, reviewer, cid, cvid, {"resolution": a.resolution, "turntable_frames": 8, "samples": 12})
        r = publish(director, cid, cvid, "minor")
        _, cv = call("GET", f"{p1.API}/characters/{cid}/versions/{cvid}", creator)
        check(f"{slug}: 1.0 published ({cv['fit_profile']})", r["version"] == "1.0", r["manifest_key"])
        pins[slug], pinned[slug] = {"code": c["code"], "version": "1.0"}, ("characters", cid, cvid)

        name, pieces = OUTFITS[slug]
        status, o = call("POST", f"{p1.API}/outfits", creator,
                         {"name": f"{name} {time.strftime('%H%M%S')}", "tags": ["phase7"],
                          "spec": {"fit_profiles": [cv["fit_profile"]], "pieces": pieces}})
        oid, ovid = o["id"], o["versions"][0]["id"]
        base = f"{p1.API}/outfits/{oid}"
        _, g = call("POST", f"{base}/drafts/{ovid}/generate", creator, {"previews": previews})
        job = wait(creator, g["job"]["id"], 1800)
        check(f"{slug}: outfit {o['code']} built and fit-checked on the reference body",
              job["status"] == "succeeded" and job["result"]["fit_ok"], json.dumps(job.get("error"))[:300])
        _, f = call("POST", f"{base}/versions/{ovid}/fits", creator, {"character_version_id": cvid, "previews": previews})
        job = wait(creator, f["job"]["id"], 1800)
        _, fit = call("GET", f"{base}/versions/{ovid}/fits/{cvid}", creator)
        check(f"{slug}: outfit fit-checked on the character", job["status"] == "succeeded" and fit["status"] == "verified",
              ", ".join(fit.get("failed") or []))
        _, d = call("POST", f"{base}/versions/{ovid}/fits/{cvid}/decision", reviewer, {"decision": "approve"})
        call("POST", f"{base}/drafts/{ovid}/submit", creator)
        call("POST", f"{base}/versions/{ovid}/review", reviewer, {"decision": "approve"})
        _, p = call("POST", f"{base}/versions/{ovid}/publish", director, {"bump": "minor"})
        job = wait(director, p["job"]["id"], 1800)
        check(f"{slug}: fit approved and outfit published 1.0",
              d["status"] == "approved" and job["status"] == "succeeded" and job["result"]["version"] == "1.0",
              json.dumps(job.get("error"))[:300] if job["status"] != "succeeded" else job["result"]["manifest_key"])
        pins[f"{slug}_outfit"], pinned[f"{slug}_outfit"] = {"code": o["code"], "version": "1.0"}, ("outfits", oid, ovid)

    # ---------------------------------------------------------------- the prop library
    for slug, (name, spec) in PROPS.items():
        o, vid, v = set_asset(creator, reviewer, director, "props", slug, name, spec, a.resolution, out)
        pins[slug], pinned[slug] = {"code": o["code"], "version": "1.0"}, ("props", o["id"], vid)
        print(f"      {v['item_ref']}: {'solid' if v['solid'] else 'not solid'}, rests on {v['rests_on']}")
    found = {cat: {x["code"] for x in call("GET", f"{p1.API}/props?category={cat}", reviewer)[1]["items"]}
             for cat in ("toy", "vehicle", "food")}
    check("the prop gallery finds props by category",
          pins["ball"]["code"] in found["toy"] and pins["car"]["code"] in found["vehicle"]
          and pins["apple"]["code"] in found["food"] and pins["ball"]["code"] not in found["vehicle"],
          ", ".join(f"{k}: {sorted(v)}" for k, v in found.items()))

    # ---------------------------------------------------------------- environments: refused, then published
    crowded = {"name": "Crowded", **ROOM, "dressing": [
        {"id": "sofa_1", "prop": {"id": "PROPD-SOFA", "version": "0.1"}, "at": [0, 1.4]},
        {"id": "sofa_2", "prop": {"id": "PROPD-SOFA", "version": "0.1"}, "at": [0.3, 1.3]}]}
    _, val = call("POST", f"{p1.API}/environments/spec/validate", creator, crowded)
    check("an environment whose dressing overlaps is refused before anything is built",
          not val["valid"] and any(i["code"] == "overlap" for i in val["issues"]),
          "; ".join(i["message"] for i in val["issues"])[:240])
    _, val = call("POST", f"{p1.API}/environments/spec/validate", creator, {"name": "Home", **ROOM})
    check("the living room's layout validates", val["valid"],
          f"{len(val['layout']['solids'])} solid items, marks {[m['name'] for m in val['layout']['marks']]}, "
          f"cameras {[c['name'] for c in val['layout']['cameras']]}")
    env, env_vid, env_v = set_asset(creator, reviewer, director, "environments", "living_room", "Home", ROOM,
                                    a.resolution, out)
    pins["env"], pinned["env"] = {"code": env["code"], "version": "1.0"}, ("environments", env["id"], env_vid)
    layout = env_v["layout"]
    (out / "environments" / "living_room-layout.json").write_text(json.dumps(layout, indent=2))
    checks = {c["name"]: c for c in env_v["check_report"]["checks"]}
    check("Blender measured the room's layout again: inside, grounded, clear, marks and door reachable",
          all(c["ok"] for c in checks.values()),
          ", ".join(sorted({n.split(":")[0] for n in checks})))
    before = library_state(creator, pinned)
    (out / "library_before.json").write_text(json.dumps(before, indent=2))

    # ---------------------------------------------------------------- the scene
    def manifest(**kw) -> dict:
        return {
            "name": "Ali and Maya at home", "description": "Phase 7 acceptance: two characters in a set",
            "frame_start": 1, "frame_end": 48, "environment": pins["env"], "lighting": "evening",
            "camera": {"mark": "wide"},
            "instances": [
                {"key": "ali", "character": pins["ali"], "outfit": pins["ali_outfit"], "mark": "rug",
                 "animations": [{"clip": "wave", "start_frame": 1}],
                 "expressions": [{"frame": 1, "expression": "happy"}],
                 "dialogue": [{"start_frame": 8, "end_frame": 40, "text": "Want to play with my car?"}]},
                {"key": "maya", "character": pins["maya"], "outfit": pins["maya_outfit"], "mark": "window",
                 "transform": {"location": [0.1, 0.0, 0.0]},
                 "animations": [{"clip": "idle", "start_frame": 1}],
                 "expressions": [{"frame": 1, "expression": "neutral"}, {"frame": 30, "expression": "excited"}]},
            ],
            "props": [
                {"key": "ball", "prop": pins["ball"], "at": [0.3, -1.4]},
                {"key": "car", "prop": pins["car"], "at": [-1.3, -1.2], "rotation_deg": 30},
                {"key": "books", "prop": pins["books"], "at": [-0.5, 0.6], "rotation_deg": 20, "on": "table_1"},
                {"key": "apple", "prop": pins["apple"], "at": [0.0, 0.6], "on": "table_1"},
            ],
            **kw}

    bad = manifest()
    bad["props"][0]["at"] = [-0.3, 1.5]  # inside the sofa
    bad["props"][3]["on"] = None  # an apple goes on a top
    bad["instances"][1]["mark"] = "kitchen"
    status, val = call("POST", f"{p1.API}/scenes/validate", creator, {"manifest": bad})
    codes = {(i["path"], i["code"]) for i in val["issues"] if i["severity"] == "error"}
    check("a prop in the sofa, an apple on the floor and an unknown mark are all refused before anything is built",
          status == 200 and not val["ok"]
          and {("props[0].at", "overlap"), ("props[3].on", "needs_surface"), ("instances[1].mark", "unknown_mark")} <= codes,
          "; ".join(f"{i['path']}: {i['code']}" for i in val["issues"]))

    m = manifest()
    status, s = call("POST", f"{p1.API}/scenes", creator, {"name": m["name"], "tags": ["phase7"], "manifest": m})
    check("scene created; characters, outfits, props and the environment pinned", status == 201,
          f"{s.get('code')}" if status == 201 else json.dumps(s)[:400])
    sid, svid = s["id"], s["versions"][0]["id"]
    base = f"{p1.API}/scenes/{sid}/versions/{svid}"
    _, v = call("GET", base, creator)
    rs = v["resolved"]
    (out / "scene_resolved.json").write_text(json.dumps(rs, indent=2))
    starts = {i["key"]: i["transform"] for i in rs["instances"]}
    heights = {p["key"]: p["z"] for p in rs["props"]}
    check("each character starts at its mark, and props on the table stand on its top",
          starts["ali"]["location"][:2] == [-0.6, -0.8] and starts["ali"]["rotation_deg"][2] == -20
          and heights["ball"] == 0 and heights["books"] > 0.3 and heights["books"] == heights["apple"],
          f"ali at {starts['ali']['location'][:2]} facing {starts['ali']['rotation_deg'][2]}°, "
          f"maya at {starts['maya']['location'][:2]}; heights {heights}; lighting {rs['lighting']} "
          f"(room's own {rs['layout']['lighting']})")
    check("the environment and every prop are pinned by exact version and file hash",
          rs["environment"]["files"]["environment.blend"] and all(p["pin"]["files"]["prop.blend"] for p in rs["props"]),
          f"{rs['environment']['code']}@{rs['environment']['version']} "
          + ", ".join(f"{p['key']}={p['pin']['code']}" for p in rs["props"]))
    _, job = call("POST", f"{base}/assemble", creator, {"previews": {
        "width": a.width, "height": a.height, "samples": 16, "frames": PREVIEW_FRAMES, "video_every": a.video_every}})
    job = wait(creator, job["job"]["id"], 3600)
    _, v = call("GET", base, creator)
    rep = v["build_report"] or {}
    check("Blender assembled scene.blend in the set", job["status"] == "succeeded" and v["build_status"] == "built",
          json.dumps(job.get("error"))[:400] if job["status"] != "succeeded" else
          f"scene.blend {v['scene_blend_sha256'][:16]}, camera {rep['camera']['name']} ({rep['camera']['lens_mm']} mm)")
    libs = {x["stored"] for x in rep["libraries"]["libraries"]}
    check("scene.blend links the environment and props from their pinned files, by relative path",
          any("environment.blend" in x for x in libs) and sum("prop.blend" in x for x in libs) == len(PROPS),
          ", ".join(sorted(libs)))
    check("the pinned sources were byte-identical after assembly", rep["sources"]["unchanged"])
    video = rep.get("video") or {}
    check("preview frames and a preview video were rendered",
          {"frames.png", "preview.mp4"} <= {f["name"] for f in v["build_files"]} and video.get("frames", 0) > 1,
          f"{video.get('frames')} video frames every {a.video_every}, {video.get('bytes')} bytes")
    for f in v["build_files"]:
        if f["name"] in ("scene_report.json", "frames.png", "preview.mp4", "thumbnail.png"):
            fetch(f["url"], out / "scene" / f["name"])

    # ---------------------------------------------------------------- reopen
    _, job = call("POST", f"{base}/reopen", creator, {"previews": {"width": a.width, "height": a.height, "samples": 16,
                                                                   "frames": PREVIEW_FRAMES}})
    job = wait(creator, job["job"]["id"], 2400)
    _, v = call("GET", base, creator)
    rep = v["verify_report"] or {}
    names = {c["name"]: c for c in rep.get("checks", [])}
    failed = [n for n, c in names.items() if not c["ok"]]
    check("reopened in a fresh workspace and matched its manifest",
          job["status"] == "succeeded" and v["verify_status"] == "verified", ", ".join(failed) or f"{len(names)} checks")
    for f in v["verify_files"]:
        fetch(f["url"], out / "scene" / f["name"])
    set_names = sorted(n for n in names if n.startswith(("set:", "prop:", "path:")))
    check("the set came back: environment from its pin, lighting, every prop grounded, inside and clear, paths clear",
          {"set:environment_linked_from_pin", "set:lighting", "set:camera_mark", "path:ali", "path:maya"} <= set(names)
          and all(f"prop:{k}:{c}" in names for k in ("ball", "car", "books", "apple") for c in ("linked_from_pin", "grounded"))
          and all(names[n]["ok"] for n in set_names), f"{len(set_names)} set checks")

    # ---------------------------------------------------------------- the library is unchanged
    after = library_state(creator, pinned)
    (out / "library_after.json").write_text(json.dumps(after, indent=2))
    for slug in pinned:
        check(f"{slug}: version unchanged (registry rows, stored bytes, status)",
              after[slug] == before[slug] and after[slug]["rows"] == after[slug]["stored"],
              f"{len(after[slug]['rows'])} files, {after[slug]['status']}")

    summary = {"pins": pins, "scene": {"scene_id": sid, "version_id": svid}, "elapsed_s": round(time.monotonic() - started)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nPhase 7 acceptance: all criteria passed in {summary['elapsed_s']} s. Files in {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
