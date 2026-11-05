"""Phase 8 acceptance check (SRS section 30, Episode Production), against a running stack.

    docker compose -f infra/compose.yaml up -d
    python scripts/phase8_acceptance.py --out /tmp/phase8

Through the API only, as the creator and director users, with characters, outfits and an
environment that are already published (by default the Phase 7 run's Ali CHAR-016 and Maya
CHAR-017 in their outfits OUTFIT-011 and OUTFIT-012, and the living room ENV-001):

* a series casts both characters with their default outfit and a voice each, and keeps the
  render settings (1920x1080, 24 fps, PNG frames, H.264 and AAC by default);
* an episode gets a script; a script with an uncast character and an unknown location is
  refused with every issue listed, then a valid one is saved as version 1;
* a storyboard is planned from the script by rule, then edited by hand (a camera push-in and
  a dissolve) and saved as a second storyboard;
* every line is spoken by espeak-ng on the media worker with its character's voice, and
  analysed for mouth shapes; the voiced storyboard lengthens shots to fit the lines;
* a music track is uploaded and normalised for the mix;
* the episode version compiles one scene version per shot, pins every asset by exact version,
  and is built in Blender, reopened in a fresh workspace and checked, then locked;
* the locked version is rendered: one Blender job per shot writes PNG frames, then FFmpeg
  joins them with the dialogue and the music into an MP4;
* the project is reopened (new logins, the episode read back by its code, every shot's scene
  rebuilt from its pins in a fresh workspace and checked), the same locked version is
  rendered again, and the two renders are compared: the same pins, scene files, staged
  source files, settings and Blender version, and the same frames, pixel for pixel;
* every library version used is unchanged.

Exits non-zero on the first failed criterion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import phase1_acceptance as p1  # noqa: E402
from phase1_acceptance import call, check, login  # noqa: E402
from phase2_acceptance import wait  # noqa: E402
from phase5_acceptance import fetch, library_state  # noqa: E402
from phase6_acceptance import upload  # noqa: E402

RENDER_DONE = ("completed", "failed", "cancelled")


class Login:
    """A user's bearer token, logged in again before it expires (Keycloak's access tokens
    last 15 minutes and the renders take longer). The helpers put a token into the header
    with an f-string, so this object stands in for the token string."""

    def __init__(self, user: str, max_age_s: float = 600):
        self.user, self.max_age_s, self.at, self.token = user, max_age_s, 0.0, ""

    def __str__(self) -> str:
        if time.monotonic() - self.at > self.max_age_s:
            self.token, self.at = login(self.user), time.monotonic()
        return self.token


def find(token: Login, plural: str, code: str) -> tuple[dict, dict]:
    """A library asset by code and its published 1.0 version (detail, version summary)."""
    _, page = call("GET", f"{p1.API}/{plural}?q={code}&limit=20", token)
    hit = next((x for x in page["items"] if x["code"] == code), None)
    check(f"{code} is in the {plural} library", hit is not None)
    _, d = call("GET", f"{p1.API}/{plural}/{hit['id']}", token)
    v = next((x for x in d["versions"] if x["status"] == "published" and x["version_label"] == "1.0"), None)
    check(f"{code}@1.0 is published", v is not None, d["name"])
    return d, v


def script(c: dict) -> dict:
    return {"title": "The Toy Car", "synopsis": "Ali asks Maya to play; she is in.",
            "scenes": [{"scene_id": "SC-001", "location_id": c["env"], "characters": [c["ali"], c["maya"]],
                        "description": "The living room, after school.",
                        "events": [{"character_id": c["maya"], "action": "wave"}],
                        "dialogue": [{"character_id": c["ali"], "text": "Hi Maya! Want to play?", "emotion": "happy"},
                                     {"character_id": c["maya"], "text": "Yes, let's go!", "emotion": "excited"}]}]}


def music_wav(path: Path, seconds: float) -> Path:
    """A soft two-chord pad, made here so the run needs no outside files."""
    chord = "+".join(f"0.12*sin(2*PI*{f}*t)" for f in (220, 277.18, 329.63))
    second = "+".join(f"0.12*sin(2*PI*{f}*t)" for f in (196, 246.94, 293.66))
    expr = f"if(lt(mod(t\\,4)\\,2)\\,{chord}\\,{second})"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc={expr}:s=44100:d={seconds}", "-af", "afade=t=in:d=0.5", str(path)], check=True)
    return path


def wait_render(token: Login, code: str, timeout_s: float) -> dict:
    deadline, last = time.monotonic() + timeout_s, ""
    while time.monotonic() < deadline:
        status, r = call("GET", f"{p1.API}/render-jobs/{code}", token)
        if status != 200:
            print(f"      GET {code}: {status} {str(r)[:200]}; trying again", flush=True)
            time.sleep(10)
            continue
        p = r["progress"]
        running = [f"{s['shot_id']} {s['job']['progress_message']}" for s in r["shots"] if s["job"]["status"] == "running"]
        msg = (f"{r['status']} {p['value']:.0%} ({p['shots_done']}/{p['shots']} shots"
               + (f", {running[0]}" if running else "")
               + (f", export {r['export_job']['status']} {r['export_job']['progress_message']}" if r["export_job"] else "")
               + ")")
        if msg != last:
            print(f"      {msg}", flush=True)
            last = msg
        if r["status"] in RENDER_DONE:
            return r
        time.sleep(5)
    raise SystemExit(f"render {code} did not finish in {timeout_s:.0f} s")


def ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json",
                          str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def output_url(token: Login, render: str, output_id: str, frame: int | None = None) -> str:
    q = f"?redirect=false{f'&frame={frame}' if frame else ''}"
    status, d = call("GET", f"{p1.API}/render-jobs/{render}/outputs/{output_id}{q}", token)
    if status != 200:
        raise SystemExit(f"output {output_id}: {status} {d}")
    return d["url"]


def shot_reports(token: Login, r: dict) -> dict[str, dict]:
    return {o["shot_id"]: json.loads(fetch(output_url(token, r["code"], o["id"])))
            for o in r["outputs"] if o["kind"] == "report" and o["shot_id"]}


def pixel_diff(a: Path, b: Path) -> float:
    """Mean absolute difference per channel, 0..1, decoded by FFmpeg."""
    def raw(p: Path) -> bytes:
        return subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(p), "-f", "rawvideo",
                               "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    x, y = raw(a), raw(b)
    if x == y:
        return 0.0
    if len(x) != len(y):
        return 1.0
    return sum(abs(i - j) for i, j in zip(x, y, strict=True)) / (255 * len(x))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=p1.API)
    ap.add_argument("--token-url", default=p1.KEYCLOAK)
    ap.add_argument("--out", default="phase8-output")
    ap.add_argument("--ali", default="CHAR-016")
    ap.add_argument("--maya", default="CHAR-017")
    ap.add_argument("--ali-outfit", default="OUTFIT-011")
    ap.add_argument("--maya-outfit", default="OUTFIT-012")
    ap.add_argument("--env", default="ENV-001")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--samples", type=int, default=4)
    ap.add_argument("--render-timeout", type=float, default=4 * 3600)
    a = ap.parse_args()
    p1.API, p1.KEYCLOAK = a.api.rstrip("/"), a.token_url
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is needed to check the video")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    creator, director = Login("creator"), Login("director")
    started = time.monotonic()

    # ---------------------------------------------------------------- the published library
    c = {"ali": a.ali, "maya": a.maya, "env": a.env}
    pinned = {}
    for slug, plural, code in (("ali", "characters", a.ali), ("maya", "characters", a.maya),
                               ("ali_outfit", "outfits", a.ali_outfit), ("maya_outfit", "outfits", a.maya_outfit),
                               ("env", "environments", a.env)):
        d, v = find(creator, plural, code)
        pinned[slug] = (plural, d["id"], v["id"])
    before = library_state(creator, pinned)
    (out / "library_before.json").write_text(json.dumps(before, indent=2))

    # ---------------------------------------------------------------- series (SRS 13)
    status, s = call("POST", f"{p1.API}/series", director, {
        "name": f"Home Stories {time.strftime('%H%M%S')}", "description": "Short episodes at Ali and Maya's home",
        "settings": {"render": {"width": a.width, "height": a.height, "samples": a.samples}},
        "members": [{"character": a.ali, "key": "ali", "outfit_version": {"code": a.ali_outfit, "version": "1.0"},
                     "voice": {"voice": "en-us", "variant": "m3", "pitch": 55, "rate_wpm": 165}},
                    {"character": a.maya, "key": "maya", "role": "supporting",
                     "outfit_version": {"code": a.maya_outfit, "version": "1.0"},
                     "voice": {"voice": "en-us", "variant": "f3", "pitch": 72, "rate_wpm": 160}}]})
    check("series created with both characters, their default outfits and voices", status == 201,
          f"{s.get('code')}: " + ", ".join(f"{m['key']}={m['character_code']} ({m['voice']['variant']})"
                                           for m in s.get("members", [])) if status == 201 else json.dumps(s)[:400])
    rs = s["settings"]["render"]
    check("the series keeps the render settings", (rs["width"], rs["height"], rs["fps"], rs["frame_format"],
                                                    rs["video_codec"], rs["audio_codec"]) ==
          (a.width, a.height, 24, "PNG", "h264", "aac"),
          f"{rs['width']}x{rs['height']} {rs['fps']} fps {rs['engine']} {rs['samples']} samples, "
          f"{rs['frame_format']} -> {rs['video_codec']} + {rs['audio_codec']}")
    env_vid = pinned["env"][2]
    _, s = call("POST", f"{p1.API}/series/{s['code']}/assets", director, [env_vid])
    check("the living room is in the series' asset collection at its exact version",
          [x["display"] for x in s["assets"]] == [f"{a.env}@1.0"])

    # ---------------------------------------------------------------- episode and script (SRS 14, 16)
    status, ep = call("POST", f"{p1.API}/episodes", director, {"series_id": s["code"], "title": "The Toy Car",
                                                               "concept": "Ali asks Maya to play"})
    check("episode created in the series", status == 201 and ep["number"] == 1, ep.get("code"))
    base = f"{p1.API}/episodes/{ep['code']}"
    good = script(c)
    bad = json.loads(json.dumps(good))
    bad["scenes"][0]["characters"] = [a.ali, "CHAR-999"]
    bad["scenes"][0]["location_id"] = "ENV-999"
    _, val = call("POST", f"{base}/scripts/validate", director, {"spec": bad})
    codes = {(i["path"], i["code"]) for i in val["issues"]}
    check("a script with an uncast character and an unknown location is refused, every issue listed",
          not val["ok"] and {("scenes[0].characters", "not_found"), ("scenes[0].location_id", "not_found"),
                             ("scenes[0].events[0].character_id", "not_in_scene")} <= codes,
          "; ".join(f"{i['path']}: {i['code']}" for i in val["issues"]))
    status, sv = call("POST", f"{base}/scripts", director, {"spec": good})
    check("script version 1 saved", status == 201 and sv["version"]["number"] == 1 and sv["version"]["source"] == "manual",
          json.dumps(sv)[:300] if status != 201 else f"{len(good['scenes'][0]['dialogue'])} lines")
    sv = sv["version"]

    # ---------------------------------------------------------------- storyboard (SRS 15)
    status, sb = call("POST", f"{base}/storyboards", director, {"script_version_id": sv["id"]})
    sb = sb["storyboard"]
    shots = sb["shots"]
    check("storyboard planned from the script by rule: an establishing wide, then a shot per line",
          status == 201 and [x["camera"]["type"] for x in shots] == ["wide", "medium", "medium"]
          and [p["mark"] for p in shots[0]["placement"]] == ["rug", "window"],
          ", ".join(f"{x['shot_id']} {x['camera']['type']} {x['duration_s']} s" for x in shots))
    edit = json.loads(json.dumps(shots))
    edit[0]["duration_s"] = 2.5
    edit[1]["camera"]["movement"] = "push_in"
    edit[2]["transition_in"] = {"kind": "dissolve", "duration_s": 0.4}
    status, sb2 = call("POST", f"{base}/storyboards", director, {
        "script_version_id": sv["id"], "source": "manual", "spec": {"shots": edit}, "based_on_id": sb["id"],
        "notes": "shorter opening, push in on Ali, dissolve to Maya"})
    check("hand edit saved as storyboard 2 (push-in, dissolve)", status == 201 and sb2["storyboard"]["number"] == 2,
          json.dumps(sb2)[:300] if status != 201 else "")
    sb2 = sb2["storyboard"]

    # ---------------------------------------------------------------- voices and music (SRS 17)
    status, vo = call("POST", f"{p1.API}/storyboards/{sb2['id']}/voice", director)
    new = [ln for ln in vo.get("lines", []) if ln["new"]]
    check("every line gets a speech clip with its character's voice (a line already spoken in that voice reuses its clip)",
          status == 202 and len(vo["lines"]) == 2 and len(vo["jobs"]) == len(new),
          f"{len(new)} new, {len(vo['lines']) - len(new)} reused; "
          + ", ".join(f"{j['params']['voice']['variant']}: {j['params']['text']}" for j in vo.get("jobs", [])))
    for j in vo["jobs"]:
        job = wait(director, j["id"], 600)
        check(f"espeak-ng spoke \"{j['params']['text']}\" and its mouth shapes were analysed",
              job["status"] == "succeeded", json.dumps(job.get("error"))[:300])
    clips = [call("GET", f"{p1.API}/audio-clips/{ln['audio']}", director)[1] for ln in vo["lines"]]
    check("the lines are ready speech clips that keep their voice and provider",
          all(x["status"] == "ready" and x["source"] == "tts" and x["provider"].startswith("espeak-ng-")
              and len(x["cues"]) > 3 for x in clips),
          ", ".join(f"{x['code']} {x['duration_s']:.2f} s {x['voice']['variant']} {len(x['cues'])} cues" for x in clips))
    status, sb3 = call("POST", f"{p1.API}/storyboards/{sb2['id']}/voiced", director)
    sb3 = sb3["storyboard"]
    fits = all(x["duration_s"] >= ln["start_s"] + clip["duration_s"] for x, clip in zip(sb3["shots"][1:], clips, strict=True)
               for ln in x["dialogue"])
    check("the voiced storyboard carries each line's clip and every shot fits its line", status == 201 and fits
          and sb3["shots"][1]["camera"]["movement"] == "push_in",
          ", ".join(f"{x['shot_id']} {x['duration_s']} s" for x in sb3["shots"]) + f", {sb3['duration_s']} s in all")
    track = upload(creator, "audio", music_wav(out / "music.wav", 12))
    status, m = call("POST", f"{p1.API}/audio-clips", creator, {"upload_id": track["id"], "kind": "music",
                                                               "name": "Home Stories theme"})
    job = wait(creator, m["job"]["id"], 600)
    _, music = call("GET", f"{p1.API}/audio-clips/{m['clip']['code']}", creator)
    check("music uploaded and normalised for the mix, with no mouth cues",
          job["status"] == "succeeded" and music["status"] == "ready" and music["cues"] is None,
          f"{music['code']} {music['duration_s']:.2f} s")

    # ---------------------------------------------------------------- episode version (SRS 16)
    status, v = call("POST", f"{base}/versions", director, {
        "storyboard_id": sb3["id"], "notes": "first cut",
        "music": [{"audio": music["code"], "volume": 0.25, "fade_in_s": 0.5, "fade_out_s": 1.0}]})
    check("episode version compiled: one scene version per shot", status == 201 and len(v["shots"]) == 3,
          json.dumps(v)[:500] if status != 201 else
          ", ".join(f"{x['shot_id']}->{x['scene_code']} v{x['scene_version']}" for x in v["shots"]))
    want = {f"{a.ali}@1.0", f"{a.maya}@1.0", f"{a.ali_outfit}@1.0", f"{a.maya_outfit}@1.0", f"{a.env}@1.0"}
    check("every asset is pinned by exact version", {x["display"] for x in v["closure"]} >= want,
          ", ".join(sorted(x["display"] for x in v["closure"])))
    check("the version holds the dialogue and the music", sorted(x["role"] for x in v["audio"]) ==
          ["dialogue", "dialogue", "music"], ", ".join(f"{x['code']} {x['role']}" for x in v["audio"]))
    (out / "episode_manifest.json").write_text(json.dumps(v["manifest"], indent=2))
    vbase = f"{p1.API}/episode-versions/{v['id']}"
    _, b = call("POST", f"{vbase}/build", director, {})
    for j in b["jobs"]:
        job = wait(director, j["id"], 3600)
        check(f"Blender assembled the scene for {j['params'].get('shot_id', j['id'])}", job["status"] == "succeeded",
              json.dumps(job.get("error"))[:400])
    _, r = call("POST", f"{vbase}/reopen", director, {})
    for j in r["jobs"]:
        job = wait(director, j["id"], 3600)
        check("the shot's scene reopened in a fresh workspace and matched its manifest", job["status"] == "succeeded",
              json.dumps(job.get("error"))[:400])
    _, v = call("GET", vbase, director)
    check("every shot built and checked", v["progress"]["built"] and v["progress"]["verified"], json.dumps(v["progress"]))
    status, v = call("POST", f"{vbase}/lock", director)
    check("episode version locked", status == 200 and v["status"] == "locked", v.get("locked_at"))

    # ---------------------------------------------------------------- render (SRS 18)
    def render(token: Login, label: str) -> dict:
        status, rj = call("POST", f"{p1.API}/render-jobs", token, {"episode_version_id": v["id"]})
        check(f"{label}: render job submitted, one Blender job per shot", status == 202 and len(rj["shots"]) == 3,
              rj.get("code") if status == 202 else json.dumps(rj)[:300])
        t = time.monotonic()
        done = wait_render(token, rj["code"], a.render_timeout)
        check(f"{label}: rendered and exported", done["status"] == "completed",
              json.dumps(done.get("error"))[:400] if done["status"] != "completed" else
              f"{done['code']} in {time.monotonic() - t:.0f} s, Blender {done['blender_version']}")
        return done

    first = render(director, "first render")
    rdir = out / "render-1"
    mp4 = rdir / "episode.mp4"
    _, dl = call("GET", f"{p1.API}/render-jobs/{first['code']}/video?redirect=false", director)
    fetch(dl["url"], mp4)
    video = next(o for o in first["outputs"] if o["kind"] == "video")
    check("the MP4 is the stored file", hashlib.sha256(mp4.read_bytes()).hexdigest() == video["sha256"],
          f"{video['size_bytes']} bytes")
    probe = ffprobe(mp4)
    (rdir / "ffprobe.json").write_text(json.dumps(probe, indent=2))
    vs = next(x for x in probe["streams"] if x["codec_type"] == "video")
    au = next(x for x in probe["streams"] if x["codec_type"] == "audio")
    expected = v["duration_s"]
    dur = float(probe["format"]["duration"])
    check("H.264 video at the series' size and 24 fps, with AAC stereo sound, the episode's length",
          vs["codec_name"] == "h264" and (vs["width"], vs["height"]) == (a.width, a.height)
          and vs["r_frame_rate"] == "24/1" and au["codec_name"] == "aac" and au["channels"] == 2
          and abs(dur - expected) < 0.15,
          f"{vs['width']}x{vs['height']} {vs['r_frame_rate']} {vs['nb_read_frames']} frames, "
          f"{au['codec_name']} {au['channels']} ch {au['sample_rate']} Hz, {dur:.2f} s (version {expected:.2f} s)")
    meta = video["meta"]
    (rdir / "episode_metadata.json").write_text(json.dumps(meta, indent=2))
    check("the export records the timeline, the audio cues, the pins and the Blender version",
          [t["transition"] for t in meta["timeline"]] == ["cut", "cut", "dissolve"]
          and {x["role"] for x in meta["cues"]} == {"dialogue", "music"}
          and meta["asset_versions"] == first["asset_versions"],
          f"{len(meta['cues'])} cues, {len(meta['asset_versions'])} pins")
    reports1 = shot_reports(director, first)
    (rdir / "render_reports.json").write_text(json.dumps(reports1, indent=2))
    stills = []
    for o in first["outputs"]:
        if o["kind"] == "frames":
            mid = (o["frame_count"] + 1) // 2
            p = rdir / f"{o['shot_id']}-{mid:04d}.png"
            fetch(output_url(director, first["code"], o["id"], mid), p)
            stills.append(p)
    _, logs = call("GET", f"{p1.API}/render-jobs/{first['code']}/logs", director)
    for lg in logs:
        if lg["url"]:
            fetch(lg["url"], rdir / "logs" / f"{lg['shot_id'] or lg['type']}.log")
    print("      seconds per frame: " + ", ".join(f"{k} {x['seconds_per_frame']}" for k, x in reports1.items()))

    # ---------------------------------------------------------------- reopen the project and render again
    director2 = Login("director")  # a new login, as when someone comes back to the project
    _, again = call("GET", f"{p1.API}/episodes/{ep['code']}", director2)
    locked = [x for x in again["versions"] if x["status"] == "locked"]
    check("project reopened: the episode is read back by its code with its locked version",
          [x["id"] for x in locked] == [v["id"]] and locked[0]["digest"] == v["digest"],
          f"{ep['code']} v{locked[0]['number']} digest {v['digest'][:16]}")
    _, r = call("POST", f"{vbase}/reopen", director2, {})
    for j in r["jobs"]:
        job = wait(director2, j["id"], 3600)
        check("each shot's scene rebuilt from its pins in a fresh workspace and checked", job["status"] == "succeeded",
              json.dumps(job.get("error"))[:400])
    second = render(director2, "re-render")
    check("the re-render used the same pinned asset versions",
          second["asset_versions"] == first["asset_versions"], ", ".join(second["asset_versions"]))
    same_scene = all(x["scene_version_id"] == y["scene_version_id"]
                     and x["job"]["params"]["scene_blend_sha256"] == y["job"]["params"]["scene_blend_sha256"]
                     for x, y in zip(first["shots"], second["shots"], strict=True))
    check("the same scene versions and scene.blend files", same_scene,
          ", ".join(f"{x['shot_id']} {x['job']['params']['scene_blend_sha256'][:12]}" for x in second["shots"]))
    reports2 = shot_reports(director2, second)
    (out / "render-2").mkdir(exist_ok=True)
    (out / "render-2" / "render_reports.json").write_text(json.dumps(reports2, indent=2))
    check("every staged source file had the same hash, and the settings and Blender version are the same",
          all(reports1[k]["sources"]["before"] == reports2[k]["sources"]["before"]
              and reports1[k]["in_force"] == reports2[k]["in_force"] for k in reports1)
          and first["settings"] == second["settings"] and first["blender_version"] == second["blender_version"],
          f"{sum(len(x['sources']['before']) for x in reports2.values())} files")
    frames1 = {o["shot_id"]: o for o in first["outputs"] if o["kind"] == "frames"}
    frames2 = {o["shot_id"]: o for o in second["outputs"] if o["kind"] == "frames"}
    total = same = exact = 0
    worst = 0.0
    for shot, o1 in frames1.items():
        o2 = frames2[shot]
        f1, f2 = o1["meta"]["frames"], o2["meta"]["frames"]
        check(f"{shot}: the same number of frames", len(f1) == len(f2), f"{len(f1)} frames")
        for k, (x, y) in enumerate(zip(f1, f2, strict=True), start=1):
            total += 1
            if x["sha256"] == y["sha256"]:
                same += 1
                continue
            pa, pb = out / "diff" / f"{shot}-{k:04d}-a.png", out / "diff" / f"{shot}-{k:04d}-b.png"
            fetch(output_url(director2, first["code"], o1["id"], k), pa)
            fetch(output_url(director2, second["code"], o2["id"], k), pb)
            d = pixel_diff(pa, pb)
            exact += d == 0.0
            worst = max(worst, d)
            if d == 0.0:  # identical pixels; the files differ only in Cycles' render-time text chunks
                pa.unlink()
                pb.unlink()
    check("the re-rendered frames match the first render pixel for pixel (mean difference under 1%)",
          worst < 0.01, f"{same + exact}/{total} frames pixel-identical ({same} byte-identical files), "
                        f"largest mean difference {worst:.5f}")
    v2 = next(o for o in second["outputs"] if o["kind"] == "video")
    print(f"      MP4: {'byte-identical' if v2['sha256'] == video['sha256'] else 're-encoded differently'} "
          f"({v2['size_bytes']} bytes)")

    # ---------------------------------------------------------------- the library is unchanged
    after = library_state(creator, pinned)
    (out / "library_after.json").write_text(json.dumps(after, indent=2))
    for slug in pinned:
        check(f"{slug}: version unchanged (registry rows, stored bytes, status)",
              after[slug] == before[slug] and after[slug]["rows"] == after[slug]["stored"],
              f"{len(after[slug]['rows'])} files, {after[slug]['status']}")

    summary = {"series": s["code"], "episode": ep["code"], "version": v["id"], "renders": [first["code"], second["code"]],
               "pins": first["asset_versions"], "duration_s": expected, "stills": [p.name for p in stills],
               "elapsed_s": round(time.monotonic() - started)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nPhase 8 acceptance: all criteria passed in {summary['elapsed_s']} s. Files in {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
