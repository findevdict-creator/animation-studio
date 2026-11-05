"""Phase 6 acceptance check (SRS section 30, Animation System), against a running stack.

    docker compose -f infra/compose.yaml up -d
    python scripts/phase6_acceptance.py --out /tmp/phase6

Through the API only, as the creator, reviewer and director users:

* publish two characters on the same rig profile: Ali (a child) and Dev (an adult);
* make five animations, each built and play-checked by Blender on the reference bodies,
  reviewed and published: a generated walk cycle, the CMU subject 2 walk uploaded as a BVH
  take and retargeted, generated talking gestures, a generated "happy" expression take, and
  a wave declared for child bodies only;
* upload a dialogue recording; the media worker normalises it and finds the mouth cues;
* build two scenes: Ali walks in, says the line with lip sync and talking gestures while his
  expression changes; Dev uses the very same walk version, then the motion-capture walk;
* show that a child-only wave is refused on Dev before anything is built;
* reopen both scenes in fresh workspaces and check them: each animation came from its pinned
  file and was fitted to that rig's legs, the walk moved the character by the stride its
  feet imply and the planted feet held, the sound strip plays the frozen recording, and the
  mouth shows the recording's shapes at their times;
* check that every published animation's files are unchanged.

Exits non-zero on the first failed criterion.
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
from phase5_acceptance import asset_file, fetch  # noqa: E402

import phase1_acceptance as p1  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CMU_WALK = ROOT / "samples" / "mocap" / "cmu_02_01_walk.bvh"
HELLO = ROOT / "samples" / "audio" / "ali_hello.wav"
LINE = "Hello there! My name is Ali. Do you want to play?"
DEV = {"name": "Dev", "body": {"age_appearance": "adult", "body_type": "average", "height_m": 1.74},
       "face": {"shape": "oval", "eye_color": "green", "mouth": "friendly"}, "hair": {"style": "short", "color": "brown"}}


def gen(gid: str, **kw) -> dict:
    return {"source": {"kind": "generator", "generator": {"id": gid, "version": "1.0"}, **kw.pop("source", {})}, **kw}


ANIMATIONS = {
    "walk": ("Easy walk", ["walk", "locomotion"], gen("GEN-WALK")),
    "talk": ("Talking hands", ["talk"], gen("GEN-TALK")),
    "happy": ("Happy face", ["expression"], gen("GEN-EXPR", source={"expression": "happy"})),
    "wave": ("Child wave", ["wave", "child"], gen("GEN-WAVE", fit_profiles=["child_average"])),
}


def upload(token: str, kind: str, path: Path) -> dict:
    req = urllib.request.Request(f"{p1.API}/uploads?kind={kind}&name={path.name}", data=path.read_bytes(),
                                 method="POST", headers={"Authorization": f"Bearer {token}",
                                                         "Content-Type": "audio/wav" if kind == "audio" else "text/plain"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def library_state(token: str, pinned: dict) -> dict:
    state = {}
    for slug, (aid, vid) in pinned.items():
        _, v = call("GET", f"{p1.API}/animations/{aid}/versions/{vid}", token)
        rows = {f["name"]: f["sha256"] for f in v["files"]}
        stored = {n: hashlib.sha256(asset_file(token, "animations", aid, vid, n)).hexdigest() for n in rows}
        state[slug] = {"status": v["status"], "rows": rows, "stored": stored}
    return state


def character(creator, reviewer, director, name, spec, res) -> tuple[dict, str]:
    status, c = call("POST", f"{p1.API}/characters", creator,
                     {"name": f"{name} {time.strftime('%H%M%S')}", "tags": ["phase6"], "spec": spec})
    check(f"{name}: character created", status == 201, f"{c.get('code')}")
    cid, cvid = c["id"], c["versions"][0]["id"]
    build_and_review(creator, reviewer, cid, cvid, {"resolution": res, "turntable_frames": 8, "samples": 12})
    r = publish(director, cid, cvid, "minor")
    _, v = call("GET", f"{p1.API}/characters/{cid}/versions/{cvid}", creator)
    check(f"{name} 1.0 published ({v['fit_profile']})", r["version"] == "1.0", r["manifest_key"])
    return c, cvid


def animation(creator, reviewer, director, slug, name, tags, spec, out) -> tuple[dict, str, dict]:
    status, a = call("POST", f"{p1.API}/animations", creator,
                     {"name": f"{name} {time.strftime('%H%M%S')}", "tags": tags, "spec": spec})
    check(f"{slug}: animation created ({a.get('category')})", status == 201, f"{a.get('code')}")
    aid, vid = a["id"], a["versions"][0]["id"]
    base = f"{p1.API}/animations/{aid}"
    _, g = call("POST", f"{base}/drafts/{vid}/generate", creator, {"previews": {"tile": 256, "samples": 12}})
    job = wait(creator, g["job"]["id"], 1800)
    _, v = call("GET", f"{base}/versions/{vid}", creator)
    rep = v["check_report"] or {}
    bodies = {b["fit_profile"]: b for b in rep.get("bodies", [])}
    c = v["compat"] or {}
    check(f"{slug}: built and play-checked by Blender on {', '.join(bodies) or 'no body'}",
          job["status"] == "succeeded" and job["result"]["check_ok"],
          json.dumps(job.get("error"))[:400] if job["status"] != "succeeded" else
          "; ".join(f"{fp}: slide {b['max_slide_cm']} cm, stretch {b['leg_stretch']}" for fp, b in bodies.items())
          + f"; {c.get('frames')} frames, {len(c.get('controls', []))} controls"
          + (f", stride {c['stride_m']:.2f} m" if c.get("travels") else "") + f"; failed: {rep.get('failed')}")
    for name_ in ("frames.png", "check_report.json"):
        asset_file(creator, "animations", aid, vid, name_, out / "animations" / f"{slug}-{name_}")
    call("POST", f"{base}/drafts/{vid}/submit", creator)
    call("POST", f"{base}/versions/{vid}/review", reviewer, {"decision": "approve", "notes": "acceptance"})
    _, p = call("POST", f"{base}/versions/{vid}/publish", director, {"bump": "minor"})
    job = wait(director, p["job"]["id"], 1800)
    check(f"{slug}: publisher re-checked animation.blend and published 1.0",
          job["status"] == "succeeded" and job["result"]["version"] == "1.0", json.dumps(job.get("error"))[:300])
    return a, vid, c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=p1.API)
    ap.add_argument("--token-url", default=p1.KEYCLOAK)
    ap.add_argument("--out", default="phase6-output")
    ap.add_argument("--resolution", type=int, default=384)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    a = ap.parse_args()
    p1.API, p1.KEYCLOAK = a.api.rstrip("/"), a.token_url
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    creator, reviewer, director = login("creator"), login("reviewer"), login("director")
    started = time.monotonic()

    # ---------------------------------------------------------------- two characters, one rig
    ali, ali_v = character(creator, reviewer, director, "Ali", ALI, a.resolution)
    dev, dev_v = character(creator, reviewer, director, "Dev", DEV, a.resolution)

    # ---------------------------------------------------------------- the animation library
    anims: dict[str, tuple[dict, str, dict]] = {}
    for slug, (name, tags, spec) in ANIMATIONS.items():
        anims[slug] = animation(creator, reviewer, director, slug, name, tags, spec, out)
    up = upload(creator, "bvh", CMU_WALK)
    check("CMU walk take uploaded", up["sha256"] == hashlib.sha256(CMU_WALK.read_bytes()).hexdigest(),
          f"{up['storage_key']} sha256 {up['sha256'][:12]}")
    spec = {"category": "locomotion", "source": {
        "kind": "bvh", "upload_key": up["storage_key"], "sha256": up["sha256"], "skeleton": "cmu", "travels": True,
        "licence": "CMU Graphics Lab Motion Capture Database, free use", "source_name": "CMU 02_01 walk"}}
    anims["mocap"] = animation(creator, reviewer, director, "mocap", "CMU walk", ["walk", "mocap"], spec, out)
    pinned = {slug: (x[0]["id"], x[1]) for slug, x in anims.items()}
    code = {slug: f"{x[0]['code']}@1.0" for slug, x in anims.items()}
    before = library_state(creator, pinned)
    (out / "library_before.json").write_text(json.dumps(before, indent=2))
    _, gallery = call("GET", f"{p1.API}/animations?category=locomotion", reviewer)
    check("the gallery finds both walks by category",
          {anims["walk"][0]["code"], anims["mocap"][0]["code"]} <= {x["code"] for x in gallery["items"]})

    # ---------------------------------------------------------------- dialogue audio
    up = upload(creator, "audio", HELLO)
    status, r = call("POST", f"{p1.API}/audio-clips", creator, {"upload_id": up["id"], "dialog_text": LINE})
    check("dialogue clip created, lipsync job on the media queue", status == 202 and r["job"]["queue"] == "media",
          r["clip"]["code"])
    job = wait(creator, r["job"]["id"], 600)
    _, clip = call("GET", f"{p1.API}/audio-clips/{r['clip']['code']}", creator)
    shapes = sorted({c["viseme"] for c in clip.get("cues") or []} - {"rest"})
    check("the media worker normalised the recording and found its mouth cues",
          job["status"] == "succeeded" and clip["status"] == "ready",
          f"{clip['duration_s']:.2f} s, {len(clip['cues'])} cues ({', '.join(shapes)}), analyser {clip['analyser']}")
    (out / "audio_clip.json").write_text(json.dumps(clip, indent=2))

    # ---------------------------------------------------------------- compatibility refuses a mismatch
    def scene_of(name, key, cv_code, uses, frame_end, **inst):
        return {"name": name, "frame_start": 1, "frame_end": frame_end, "camera": {"preset": "wide", "angle_deg": 70},
                "instances": [{"key": key, "character": {"code": cv_code, "version": "1.0"}, "animations": uses, **inst}]}
    bad = scene_of("Dev waves", "dev", dev["code"], [{"clip": code["wave"], "start_frame": 1}], 48)
    status, val = call("POST", f"{p1.API}/scenes/validate", creator, {"manifest": bad})
    check("a wave declared for child bodies is refused on the adult before anything is built",
          status == 200 and not val["ok"] and any(i["code"] == "incompatible" for i in val["issues"]),
          "; ".join(i["message"] for i in val["issues"])[:240])

    # ---------------------------------------------------------------- two scenes, one walk
    talk_start = 60
    scenes = {
        "ali_walks_and_talks": scene_of(
            "Ali walks in and says hello", "ali", ali["code"],
            [{"clip": code["walk"], "start_frame": 1, "repeat": 2},
             {"clip": code["talk"], "start_frame": talk_start, "repeat": 3},
             {"clip": code["happy"], "start_frame": 150}], 200,
            expressions=[{"frame": 1, "expression": "neutral"}, {"frame": 56, "expression": "happy"},
                         {"frame": 120, "expression": "excited", "weight": 0.8}],
            dialogue=[{"start_frame": talk_start, "end_frame": 170, "text": LINE, "audio": clip["code"]}],
            state={"mood": "cheerful"}),
        "dev_walks": scene_of(
            "Dev walks the same walk", "dev", dev["code"],
            [{"clip": code["walk"], "start_frame": 1, "repeat": 2}, {"clip": code["mocap"], "start_frame": 64}], 140,
            transform={"location": [0.6, 0, 0]}),
    }
    frames = {"ali_walks_and_talks": [1, 28, 70, 100, 130, 180], "dev_walks": [1, 30, 70, 100, 130]}
    built = {}
    for slug, m in scenes.items():
        status, s = call("POST", f"{p1.API}/scenes", creator, {"name": m["name"], "tags": ["phase6"], "manifest": m})
        check(f"{slug}: scene created; its animations pinned and compatible with the rig",
              status == 201, f"{s.get('code')}" if status == 201 else json.dumps(s)[:400])
        sid, svid = s["id"], s["versions"][0]["id"]
        base = f"{p1.API}/scenes/{sid}/versions/{svid}"
        _, v = call("GET", base, creator)
        inst = v["resolved"]["instances"][0]
        check(f"{slug}: each library animation is pinned by exact version and file hash",
              all(u["source"] == "library" and u["pin"]["files"]["animation.blend"] for u in inst["animations"]),
              ", ".join(f"{u['clip']} {u['pin']['files']['animation.blend'][:10]}" for u in inst["animations"]))
        _, job = call("POST", f"{base}/assemble", creator,
                      {"previews": {"width": a.width, "height": a.height, "samples": 16, "frames": frames[slug]}})
        job = wait(creator, job["job"]["id"], 2400)
        _, v = call("GET", base, creator)
        rep = v["build_report"] or {}
        check(f"{slug}: Blender assembled scene.blend", job["status"] == "succeeded" and v["build_status"] == "built",
              json.dumps(job.get("error"))[:400] if job["status"] != "succeeded" else
              f"scene.blend {v['scene_blend_sha256'][:16]}")
        clips = rep["instances"][0]["animations"]
        check(f"{slug}: every animation fitted to {inst['character']['code']}'s legs",
              all(c.get("source") != "library" or c.get("leg_ratio") for c in clips if "track" in c),
              "; ".join(f"{c['clip']} x{c['leg_ratio']}" + (f", travels {c['travel_m']} m" if c.get("travel_m") else "")
                        for c in clips if c.get("source") == "library"))
        for f in v["build_files"]:
            if f["name"] in ("scene.blend", "scene_report.json", "frames.png"):
                fetch(f["url"], out / slug / f["name"])
        built[slug] = (sid, svid, base)

    # ---------------------------------------------------------------- reopen and measure
    reports = {}
    for slug, (sid, svid, base) in built.items():
        _, job = call("POST", f"{base}/reopen", creator, {"previews": {"width": a.width, "height": a.height,
                                                                        "samples": 16, "frames": frames[slug]}})
        job = wait(creator, job["job"]["id"], 2400)
        _, v = call("GET", base, creator)
        rep = v["verify_report"] or {}
        failed = [c["name"] for c in rep.get("checks", []) if not c["ok"]]
        check(f"{slug}: reopened in a fresh workspace and matched its manifest",
              job["status"] == "succeeded" and v["verify_status"] == "verified",
              ", ".join(failed) or f"{len(rep.get('checks', []))} checks")
        for f in v["verify_files"]:
            fetch(f["url"], out / slug / f["name"])
        reports[slug] = {c["name"]: c for c in rep["checks"]}

    def get(slug, suffix):
        return next(c for n, c in reports[slug].items() if n.endswith(suffix))

    for slug, who in (("ali_walks_and_talks", "Ali"), ("dev_walks", "Dev")):
        walk = get(slug, f"travels:{code['walk']}")
        feet = get(slug, f"feet_planted:{code['walk']}")
        check(f"{who} walks: the root travelled the stride the feet imply and the planted feet held",
              walk["ok"] and feet["ok"], f"moved {walk['moved_m']} m (expected {walk['expected_m']}), "
                                         f"worst slide {feet['max_slide_cm']} cm (limit {feet['limit_cm']})")
    ratios = [get(s, f"animation:{code['walk']}")["leg_ratio"] for s in built]
    check("the same walk version plays on two characters, scaled to each one's legs",
          ratios[1] > ratios[0] * 1.2, f"leg ratio Ali {ratios[0]}, Dev {ratios[1]}")
    mocap = get("dev_walks", f"feet_planted:{code['mocap']}")
    check("the motion-capture walk plays on Dev with its feet planted", mocap["ok"],
          f"worst slide {mocap['max_slide_cm']} cm (limit {mocap['limit_cm']})")
    audio = get("ali_walks_and_talks", f"dialogue_audio:ali:{clip['code']}")
    mouth = get("ali_walks_and_talks", "ali:dialogue_mouth_moves")
    check("Ali talks: the recording plays from the scene and the mouth follows it",
          audio["ok"] and mouth["ok"] and mouth.get("audio") == clip["code"],
          f"{audio['stored']}; frame {mouth['frame']}: vis_{mouth['viseme'].removeprefix('vis_')} at {mouth['prop']}")
    exprs = get("ali_walks_and_talks", "ali:expressions_play")
    check("Ali's expressions play (neutral, happy, excited, then the happy take)", exprs["ok"],
          f"{exprs['keys']} expression keys measured on the face")
    _, usage = call("GET", f"{p1.API}/scenes/usage/{anims['walk'][1]}", reviewer)
    check("the walk version is pinned in both scenes, by reference",
          sorted(u["instance_key"] for u in usage) == ["ali", "dev"],
          ", ".join(f"{u['scene_code']} {u['instance_key']}" for u in usage))

    # ---------------------------------------------------------------- the library is unchanged
    after = library_state(creator, pinned)
    (out / "library_after.json").write_text(json.dumps(after, indent=2))
    for slug in pinned:
        check(f"{slug}: animation version unchanged (registry rows, stored bytes, status)",
              after[slug] == before[slug] and after[slug]["rows"] == after[slug]["stored"],
              f"{len(after[slug]['rows'])} files, {after[slug]['status']}")

    summary = {"characters": {"ali": ali["code"], "dev": dev["code"]}, "animations": code, "audio": clip["code"],
               "scenes": {k: {"scene_id": s, "version_id": v} for k, (s, v, _) in built.items()},
               "elapsed_s": round(time.monotonic() - started)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nPhase 6 acceptance: all criteria passed in {summary['elapsed_s']} s. Files in {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
