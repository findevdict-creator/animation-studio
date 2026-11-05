"""Lip sync from dialogue audio (SRS 30 Phase 6), for real: ffmpeg, the analyser, the media
worker, and a scene whose face follows the recording.

The recording is ``samples/audio/ali_hello.wav`` (espeak-ng: "Hello there! My name is Ali.
Do you want to play?"), so the test knows where the words are."""

import hashlib
import json
import shutil
import stat
import subprocess
import uuid
from pathlib import Path

import pytest
from app.config import get_settings
from app.models.jobs import Job
from studio_contracts.jobs import JobStatus
from studio_workers import lipsync as L
from studio_workers.media_runner import MediaRunner

ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "samples" / "audio" / "ali_hello.wav"
TEXT = "Hello there! My name is Ali. Do you want to play?"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def normalised(tmp_path) -> tuple[Path, float]:
    wav = tmp_path / "dialogue.wav"
    return wav, L.normalise(HELLO, wav)


def test_normalise_is_16k_mono_and_deterministic(tmp_path):
    wav, duration = normalised(tmp_path)
    import wave

    with wave.open(str(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
    assert 4.0 < duration < 4.6
    again = tmp_path / "again.wav"
    L.normalise(HELLO, again)
    assert hashlib.sha256(wav.read_bytes()).digest() == hashlib.sha256(again.read_bytes()).digest()
    with pytest.raises(L.AudioRefused, match="could not read"):
        bad = tmp_path / "bad.wav"
        bad.write_bytes(b"RIFF....not audio")
        L.normalise(bad, tmp_path / "x.wav")


def test_phonetic_analyser_follows_the_words(tmp_path):
    wav, duration = normalised(tmp_path)
    segments = L.voiced_segments(L.loudness(L.read_samples(wav)))
    assert len(segments) == 3  # three sentences, two pauses
    r = L.analyse(wav, duration, TEXT, tmp_path)
    assert r.analyser == L.PHONETIC and r.recognizer == "phonetic" and r.dialog_text == TEXT
    cues = r.cues
    assert cues[0].start_s == 0 and abs(cues[-1].end_s - duration) < 0.02
    assert all(a.end_s == b.start_s for a, b in zip(cues, cues[1:], strict=False))  # no gaps, no overlaps
    # the mouth rests in each pause and moves in each sentence
    for (_, a1), (b0, _) in zip(segments, segments[1:], strict=False):
        assert L.viseme_at(cues, (a1 + b0) / 2) == "rest"
    for a, b in segments:
        assert {c.viseme for c in cues if a <= c.start_s < b} - {"rest"}
    # "Hello there": opens on E, closes for the "M" of "My" only in the second sentence
    first = [c.viseme for c in cues if segments[0][0] <= c.start_s < segments[0][1]]
    second = [c.viseme for c in cues if segments[1][0] <= c.start_s < segments[1][1]]
    assert first[0] == "E" and "TH" in first and "MBP" not in first
    assert second[0] == "MBP" and "L" in second
    # without a transcript the mouth still opens on the loud parts
    loud = L.analyse(wav, duration, None, tmp_path)
    assert loud.dialog_text is None and {c.viseme for c in loud.cues} >= {"AI", "rest"}


def test_silence_is_refused(tmp_path):
    silent = tmp_path / "silent.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                    "-t", "1", str(silent)], check=True)
    wav = tmp_path / "n.wav"
    d = L.normalise(silent, wav)
    with pytest.raises(L.AudioRefused, match="no speech"):
        L.analyse(wav, d, "Hello", tmp_path)


def test_rhubarb_when_configured(tmp_path):
    """The Rhubarb path, with a stand-in executable that answers like Rhubarb 1.14 does: the
    command line it gets, the dialog file, and the shape mapping. (The real binary is not run
    by this test.)"""
    wav, duration = normalised(tmp_path)
    fake = tmp_path / "rhubarb"
    log = tmp_path / "argv.json"
    fake.write_text(f"""#!/usr/bin/env python3
import json, sys
if sys.argv[1:] == ["--version"]:
    print("Rhubarb Lip Sync version 1.14.0"); sys.exit(0)
json.dump(sys.argv[1:], open({str(log)!r}, "w"))
out = sys.argv[sys.argv.index("-o") + 1]
json.dump({{"metadata": {{}}, "mouthCues": [{{"start": 0.0, "end": 0.05, "value": "X"}},
          {{"start": 0.05, "end": 0.3, "value": "C"}}, {{"start": 0.3, "end": 0.4, "value": "A"}},
          {{"start": 0.4, "end": {duration}, "value": "X"}}]}}, open(out, "w"))
""")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    r = L.analyse(wav, duration, TEXT, tmp_path, rhubarb=str(fake))
    assert r.analyser == "rhubarb-1.14.0" and r.recognizer == "pocketSphinx"
    assert [(c.shape, c.viseme) for c in r.cues] == [("X", "rest"), ("C", "E"), ("A", "MBP"), ("X", "rest")]
    argv = json.loads(log.read_text())
    assert argv[-1] == str(wav) and "-d" in argv and (tmp_path / "dialog.txt").read_text() == TEXT
    assert argv[argv.index("--recognizer") + 1] == "pocketSphinx" and argv[argv.index("-f") + 1] == "json"


def test_lipsync_job_records_the_clip(api_as, db, storage_as, dispatcher, tmp_path):
    settings = get_settings().model_copy(update={"work_root": str(tmp_path / "work")})
    creator = api_as("creator")
    up = creator.post("/api/v1/uploads", params={"kind": "audio", "name": HELLO.name}, content=HELLO.read_bytes(),
                      headers={"content-type": "audio/wav"}).json()
    r = creator.post("/api/v1/audio-clips", json={"upload_id": up["id"], "dialog_text": TEXT}).json()
    out = MediaRunner(settings, work_storage=storage_as("worker", "work"), dispatcher=dispatcher).run(
        uuid.UUID(r["job"]["id"]))
    with db() as s:
        job = s.get(Job, uuid.UUID(r["job"]["id"]))
        assert out.status == JobStatus.succeeded, job.error
        result = job.result
    clip = creator.get(f"/api/v1/audio-clips/{r['clip']['code']}").json()
    assert clip["status"] == "ready" and clip["analyser"] == L.PHONETIC and 4.0 < clip["duration_s"] < 4.6
    assert result["status"] == "ready" and set(result["data"]["visemes"]) >= {"E", "MBP", "L", "O"}
    work = storage_as("api", "work")
    data = work.get_bytes(f"jobs/{r['job']['id']}/attempt-1/out/dialogue.wav")
    assert hashlib.sha256(data).hexdigest() == clip["wav_sha256"]
    assert not list((tmp_path / "work").iterdir())  # the workspace is gone

    # an unusable recording fails the clip and the job, once
    junk = creator.post("/api/v1/uploads", params={"kind": "audio", "name": "junk.wav"},
                        content=b"RIFF\x00\x00\x00\x00WAVEjunk", headers={"content-type": "audio/wav"}).json()
    r = creator.post("/api/v1/audio-clips", json={"upload_id": junk["id"]}).json()
    out = MediaRunner(settings, work_storage=storage_as("worker", "work"), dispatcher=dispatcher).run(
        uuid.UUID(r["job"]["id"]))
    assert out.status == JobStatus.failed
    clip = creator.get(f"/api/v1/audio-clips/{r['clip']['id']}").json()
    assert clip["status"] == "failed" and "ffmpeg could not read" in clip["error"]


def test_scene_face_follows_the_recording(api_as, db, storage_as, dispatcher, tmp_path):
    """A published Ali says the line with a published talk animation: the scene is assembled
    with the recording as a sound strip, then reopened, and the mouth shows the recording's
    visemes at their times."""
    pytest.importorskip("bpy", reason="needs the bpy 5.2 wheel")
    from studio_workers.publisher import PublisherRunner
    from studio_workers.supervisor import Supervisor

    settings = get_settings().model_copy(update={"work_root": str(tmp_path / "work"), "blender_mode": "wheel"})
    creator, reviewer, director = api_as("creator"), api_as("reviewer"), api_as("director")

    def run(job, runner="blender"):
        if runner == "publisher":
            r = PublisherRunner(settings, work_storage=storage_as("publisher", "work"),
                                library_storage=storage_as("publisher", "library"), dispatcher=dispatcher)
        elif runner == "media":
            r = MediaRunner(settings, work_storage=storage_as("worker", "work"), dispatcher=dispatcher)
        else:
            r = Supervisor(settings, work_storage=storage_as("worker", "work"), dispatcher=dispatcher)
        out = r.run(uuid.UUID(job["id"]))
        with db() as s:
            j = s.get(Job, uuid.UUID(job["id"]))
            assert out.status == JobStatus.succeeded, j.error
            return j.result

    c = creator.post("/api/v1/characters", json={"name": f"Ali {uuid.uuid4().hex[:6]}", "spec": {
        "name": "Ali", "body": {"age_appearance": "child", "height_m": 1.31}, "hair": {"style": "spiky"}}}).json()
    cid, cvid = c["id"], c["versions"][0]["id"]
    run(creator.post(f"/api/v1/characters/{cid}/drafts/{cvid}/generate", json={"previews": {
        "resolution": 96, "samples": 1, "turntable_frames": 4, "sheet_tile": 64}}).json()["job"])
    creator.post(f"/api/v1/characters/{cid}/drafts/{cvid}/submit")
    reviewer.post(f"/api/v1/characters/{cid}/versions/{cvid}/review", json={"decision": "approve"})
    run(director.post(f"/api/v1/characters/{cid}/versions/{cvid}/publish").json()["job"], "publisher")

    a = creator.post("/api/v1/animations", json={"name": "Talk", "spec": {
        "source": {"kind": "generator", "generator": {"id": "GEN-TALK", "version": "1.0"}}}}).json()
    base = f"/api/v1/animations/{a['id']}"
    vid = a["versions"][0]["id"]
    assert run(creator.post(f"{base}/drafts/{vid}/generate", json={"previews": {"tile": 64, "samples": 1}}).json()["job"])[
        "check_ok"]
    creator.post(f"{base}/drafts/{vid}/submit")
    reviewer.post(f"{base}/versions/{vid}/review", json={"decision": "approve"})
    run(director.post(f"{base}/versions/{vid}/publish").json()["job"], "publisher")

    up = creator.post("/api/v1/uploads", params={"kind": "audio", "name": HELLO.name}, content=HELLO.read_bytes(),
                      headers={"content-type": "audio/wav"}).json()
    r = creator.post("/api/v1/audio-clips", json={"upload_id": up["id"], "dialog_text": TEXT}).json()
    run(r["job"], "media")
    clip = creator.get(f"/api/v1/audio-clips/{r['clip']['code']}").json()

    m = {"name": "Ali says hello", "frame_start": 1, "frame_end": 120, "camera": {"preset": "medium"},
         "instances": [{"key": "ali", "character": {"code": c["code"], "version": "1.0"},
                        "expressions": [{"frame": 1, "expression": "happy"}, {"frame": 60, "expression": "excited"}],
                        "animations": [{"clip": f"{a['code']}@1.0", "start_frame": 10, "repeat": 3}],
                        "dialogue": [{"start_frame": 10, "end_frame": 118, "text": TEXT, "audio": clip["code"]}]}]}
    r = creator.post("/api/v1/scenes", json={"name": m["name"], "manifest": m})
    assert r.status_code == 201, r.text
    s = r.json()
    sbase = f"/api/v1/scenes/{s['id']}/versions/{s['versions'][0]['id']}"
    result = run(creator.post(f"{sbase}/assemble", json={"previews": {"width": 192, "height": 108, "samples": 1}}
                              ).json()["job"])
    assert result["build_status"] == "built", result
    result = run(creator.post(f"{sbase}/reopen", json={"previews": {"width": 192, "height": 108, "samples": 1}}
                              ).json()["job"])
    checks = {c["name"]: c for c in result["data"]["report"]["checks"]}
    assert result["verify_status"] == "verified", [c for c in checks.values() if not c["ok"]]
    audio = checks[f"dialogue_audio:ali:{clip['code']}"]
    assert audio["stored"] == f"//../audio/{clip['code']}/dialogue.wav" and audio["sha256"] == clip["wav_sha256"]
    talking = next(c for n, c in checks.items() if n.startswith("ali:") and c.get("audio") == clip["code"])
    assert talking["prop"] > 0.5, talking


def test_a_configured_rhubarb_that_is_missing_is_an_error(tmp_path):
    wav, duration = normalised(tmp_path)
    with pytest.raises(RuntimeError, match="not an executable"):
        L.analyse(wav, duration, TEXT, tmp_path, rhubarb=str(tmp_path / "no-such-rhubarb"))


@pytest.mark.skipif(shutil.which("rhubarb") is None, reason="Rhubarb Lip Sync is not installed here")
def test_real_rhubarb(tmp_path):
    wav, duration = normalised(tmp_path)
    r = L.analyse(wav, duration, TEXT, tmp_path, rhubarb=shutil.which("rhubarb"))
    assert r.analyser == "rhubarb-1.14.0" and r.cues[0].start_s == 0
    assert {c.viseme for c in r.cues} - {"rest"}
