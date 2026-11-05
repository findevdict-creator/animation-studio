# ruff: noqa: F811  (pytest fixtures imported from the API tests)
"""Episode media for real (Phase 8): lines spoken by espeak-ng through the media worker with
each character's voice, music normalised for the mix, and the FFmpeg export of a render
job's PNG sequences with a dissolve, the dialogue and the music into an H.264/AAC MP4.

The frames here are FFmpeg test patterns at the size the render settings ask for; the
Phase 8 acceptance script renders them with Blender."""

import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))  # the API tests' helpers and fixtures

import pytest
from app.assets import renders as rsvc  # noqa: E402
from app.config import get_settings
from app.jobs import service
from app.models.jobs import Job
from studio_contracts.jobs import JobStatus
from studio_workers.media_runner import MediaRunner
from tests.test_episode_library import (  # noqa: F401  (episode and world are fixtures)
    build_all,
    episode,
    make_storyboard,
    voiced_storyboard,
    world,
)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("espeak-ng") is None,
                                reason="needs ffmpeg and espeak-ng")
ROOT = Path(__file__).resolve().parents[2]


def runner(tmp_path, storage_as, dispatcher):
    settings = get_settings().model_copy(update={"work_root": str(tmp_path / "work")})
    return MediaRunner(settings, work_storage=storage_as("worker", "work"), dispatcher=dispatcher)


def test_lines_are_spoken_with_each_characters_voice(api_as, db, episode, storage_as, dispatcher, tmp_path):
    director = api_as("director")
    _, sb = make_storyboard(director, episode)
    r = director.post(f"/api/v1/storyboards/{sb['id']}/voice").json()
    m = runner(tmp_path, storage_as, dispatcher)
    for job in r["jobs"]:
        out = m.run(uuid.UUID(job["id"]))
        with db() as s:
            assert out.status == JobStatus.succeeded, s.get(Job, uuid.UUID(job["id"])).error
    ali, maya = (api_as("reviewer").get(f"/api/v1/audio-clips/{ln['audio']}").json() for ln in r["lines"])
    assert ali["status"] == maya["status"] == "ready" and ali["provider"].startswith("espeak-ng-1.")
    assert ali["voice"]["variant"] == "m3" and maya["voice"]["variant"] == "f3" and maya["source"] == "tts"
    assert 0.8 < ali["duration_s"] < 3.0 and len(maya["cues"]) > 3
    assert {c["viseme"] for c in maya["cues"]} - {"rest"}
    # the same text and voice is spoken the same way: espeak-ng is deterministic
    work = storage_as("api", "work")
    with db() as s:
        key = s.get(Job, uuid.UUID(r["jobs"][0]["id"])).result["data"]["dialogue_wav"]["storage_key"]
    assert hashlib.sha256(work.get_bytes(key)).hexdigest() == ali["wav_sha256"]
    voiced = director.post(f"/api/v1/storyboards/{sb['id']}/voiced")
    assert voiced.status_code == 201, voiced.text
    shot = voiced.json()["storyboard"]["shots"][1]
    assert shot["dialogue"][0]["audio"] == ali["code"] and shot["duration_s"] >= 0.3 + ali["duration_s"]
    assert not list((tmp_path / "work").iterdir())


def test_an_unknown_voice_fails_the_clip(api_as, db, episode, storage_as, dispatcher, tmp_path):
    from app.assets import episodes as svc
    from studio_contracts.episode import VoiceProfile

    with db.begin() as s:
        clip = svc.create_tts_clip(s, name="x", text_="Hello", voice=VoiceProfile(voice="xx-nowhere"), created_by=None)
        job, _ = service.submit(s, "synthesize_speech", {"audio_id": str(clip.id), "text": "Hello",
                                                         "voice": clip.voice})
        clip.job_id = job.id
        jid, code = job.id, clip.code
    assert runner(tmp_path, storage_as, dispatcher).run(jid).status == JobStatus.failed
    c = api_as("reviewer").get(f"/api/v1/audio-clips/{code}").json()
    assert c["status"] == "failed" and "no voice 'xx-nowhere'" in c["error"]


def music_upload(api, tmp_path, seconds=6):
    path = tmp_path / "music.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=330:duration={seconds}", "-f", "lavfi", "-i",
                    f"sine=frequency=495:duration={seconds}", "-filter_complex", "amix=inputs=2,volume=0.5",
                    "-ar", "44100", str(path)], check=True)
    r = api.post("/api/v1/uploads", params={"kind": "audio", "name": "music.wav"}, content=path.read_bytes(),
                 headers={"content-type": "audio/wav"})
    assert r.status_code == 201, r.text
    return r.json()


def test_music_is_normalised_without_mouth_cues(api_as, db, storage_as, dispatcher, tmp_path):
    creator = api_as("creator")
    up = music_upload(creator, tmp_path)
    r = creator.post("/api/v1/audio-clips", json={"upload_id": up["id"], "kind": "music", "name": "Theme"}).json()
    out = runner(tmp_path, storage_as, dispatcher).run(uuid.UUID(r["job"]["id"]))
    assert out.status == JobStatus.succeeded
    c = creator.get(f"/api/v1/audio-clips/{r['clip']['code']}").json()
    assert c["kind"] == "music" and c["status"] == "ready" and c["cues"] is None and 5.9 < c["duration_s"] < 6.1
    assert [x["code"] for x in creator.get("/api/v1/audio-clips", params={"kind": "music"}).json()["items"]] == [c["code"]]


def frames(dir_: Path, n: int, w: int, h: int, color: str):
    dir_.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size={w}x{h}:rate=24,drawbox=c={color}@0.5:t=fill", "-frames:v", str(n),
                    "-start_number", "1", str(dir_ / "frame_%04d.png")], check=True)
    return sorted(dir_.glob("frame_*.png"))


def test_export_joins_the_shots_and_mixes_the_audio(api_as, db, episode, storage_as, dispatcher, tmp_path):
    director = api_as("director")
    m = runner(tmp_path, storage_as, dispatcher)
    # voiced for real, plus a music bed
    _, sb = make_storyboard(director, episode)
    for job in director.post(f"/api/v1/storyboards/{sb['id']}/voice").json()["jobs"]:
        assert m.run(uuid.UUID(job["id"])).status == JobStatus.succeeded
    sb = director.post(f"/api/v1/storyboards/{sb['id']}/voiced").json()["storyboard"]
    creator = api_as("creator")  # a creator brings the music and effects into the library
    up = music_upload(creator, tmp_path, seconds=20)
    music = creator.post("/api/v1/audio-clips", json={"upload_id": up["id"], "kind": "music"}).json()
    assert m.run(uuid.UUID(music["job"]["id"])).status == JobStatus.succeeded
    sfx = creator.post("/api/v1/audio-clips", json={"upload_id": up["id"], "kind": "sfx"}).json()
    assert m.run(uuid.UUID(sfx["job"]["id"])).status == JobStatus.succeeded
    # the last shot dissolves in over the one before
    shots = [dict(s) for s in sb["shots"]]
    shots[2] = {**shots[2], "transition_in": {"kind": "dissolve", "duration_s": 0.5}}
    sv = director.get(f"/api/v1/episodes/{episode['episode']['code']}").json()["script_versions"][0]
    sb = director.post(f"/api/v1/episodes/{episode['episode']['code']}/storyboards", json={
        "script_version_id": sv["id"], "source": "manual", "spec": {"shots": shots}}).json()["storyboard"]
    v = director.post(f"/api/v1/episodes/{episode['episode']['code']}/versions", json={
        "storyboard_id": sb["id"], "render": {"width": 160, "height": 90},
        "music": [{"audio": music["clip"]["code"], "volume": 0.3, "fade_in_s": 0.5, "fade_out_s": 1.0}],
        "sfx": [{"audio": sfx["clip"]["code"], "start_s": 1.0, "volume": 0.2}]})
    assert v.status_code == 201, v.text
    v = v.json()
    build_all(api_as, db, v)
    assert director.post(f"/api/v1/episode-versions/{v['id']}/lock").status_code == 200
    rj = director.post("/api/v1/render-jobs", json={"episode_version_id": v["id"]}).json()
    assert rj["settings"]["width"] == 160

    # the shot jobs' outcomes, with real PNG sequences in the work bucket
    store = storage_as("worker", "work")
    out = None
    for k, s in enumerate(rj["shots"]):
        n = round(v["shots"][k]["duration_s"] * 24)
        files = frames(tmp_path / "frames" / s["shot_id"], n, 160, 90, ["red", "green", "blue"][k])
        uploaded = []
        for f in files:
            key = f"jobs/{s['job']['id']}/attempt-1/out/frames/{f.name}"
            sha = hashlib.sha256(f.read_bytes()).hexdigest()
            store.put_file(key, f, "image/png", sha)
            uploaded.append({"path": f"frames/{f.name}", "role": "frame", "mime_type": "image/png",
                             "storage_key": key, "sha256": sha, "size_bytes": f.stat().st_size})
        with db.begin() as sess:
            service.claim(sess, uuid.UUID(s["job"]["id"]), "test")
            out = rsvc.record_shot(sess, uuid.UUID(s["job"]["id"]), uploaded, {"seconds_per_frame": 0.1}, "5.2.3")
            service.finish(sess, uuid.UUID(s["job"]["id"]), JobStatus.succeeded, result={})
    export_id = uuid.UUID(out["_dispatch"][0][0])
    assert out["_dispatch"][0][1] == "media"
    res = m.run(export_id)
    with db() as sess:
        assert res.status == JobStatus.succeeded, sess.get(Job, export_id).error
    done = director.get(f"/api/v1/render-jobs/{rj['code']}").json()
    assert done["status"] == "completed" and done["progress"]["value"] == 1.0
    video = next(o for o in done["outputs"] if o["kind"] == "video")
    meta = video["meta"]
    expected = sum(s["duration_s"] for s in v["shots"]) - 0.5  # the dissolve overlaps
    assert meta["video"]["codec"] == "h264" and (meta["video"]["width"], meta["video"]["height"]) == (160, 90)
    assert meta["video"]["fps"] == 24 and meta["audio"]["codec"] == "aac" and meta["audio"]["channels"] == 2
    assert abs(meta["duration_s"] - expected) < 0.1 and abs(meta["video"]["frames"] - expected * 24) <= 2
    assert [t["transition"] for t in meta["timeline"]] == ["cut", "cut", "dissolve"]
    assert {c["role"] for c in meta["cues"]} == {"dialogue", "music", "sfx"}
    assert meta["asset_versions"] == rj["asset_versions"] and meta["blender_version"] == "5.2.3"
    mp4 = tmp_path / "episode.mp4"
    mp4.write_bytes(storage_as("api", "work").get_bytes(done["output_location"]))
    assert hashlib.sha256(mp4.read_bytes()).hexdigest() == video["sha256"]
    # the mix is not silent where Ali speaks
    line = next(c for c in meta["cues"] if c["role"] == "dialogue")
    level = loudness(mp4, line["start_s"] + 0.1, 0.5)
    assert level > -40, level
    report = next(o for o in done["outputs"] if o["kind"] == "report" and o["shot_id"] is None)
    doc = json.loads(storage_as("api", "work").get_bytes(report["storage_key"]))
    assert doc["render_job"] == rj["code"] and len(doc["shots"]) == 3


def loudness(path: Path, start: float, dur: float) -> float:
    run = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i",
                          str(path), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    line = next(ln for ln in run.stderr.splitlines() if "mean_volume" in ln)
    return float(line.split("mean_volume:")[1].split()[0])
