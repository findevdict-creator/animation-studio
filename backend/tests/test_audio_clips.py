"""Dialogue clips for lip sync (SRS 30 Phase 6): an audio upload becomes AUDIO-NNN with a
lipsync job on the media queue; the job's outcome is written once; a scene's dialogue line
freezes a ready clip's WAV hash and cues, and refuses a clip that is not ready.

The outcome is recorded with the service call the media worker makes
(``workers/tests/test_lipsync.py`` runs the real job)."""

import io
import uuid
import wave

import numpy as np
import pytest
from app.assets import audio as svc
from app.models.audio import AudioClip
from sqlalchemy import text
from studio_contracts.audio import LipSync, MouthCue

from tests.test_scene_library import cast, manifest  # noqa: F401
from tests.test_scene_library import create as create_scene


def tone_wav(seconds=1.0, rate=16000) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    pcm = (np.sin(2 * np.pi * 220 * t) * 8000).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def upload(api, data=None, name="line.wav"):
    r = api.post("/api/v1/uploads", params={"kind": "audio", "name": name}, content=data or tone_wav(),
                 headers={"content-type": "audio/wav"})
    assert r.status_code == 201, r.text
    return r.json()


def finish(db, clip_id, job_id, ok=True, duration=1.0):
    result = LipSync(analyser="studio-phonetic-1.0", recognizer="phonetic", dialog_text="Hello", duration_s=duration,
                     cues=(MouthCue(start_s=0, end_s=0.1, viseme="rest", shape="rest"),
                           MouthCue(start_s=0.1, end_s=0.5, viseme="E", shape="E"),
                           MouthCue(start_s=0.5, end_s=0.8, viseme="O", shape="O"),
                           MouthCue(start_s=0.8, end_s=duration, viseme="rest", shape="rest")))
    with db.begin() as s:
        clip = s.get(AudioClip, uuid.UUID(clip_id), with_for_update=True)
        wav = {"storage_key": f"jobs/{job_id}/attempt-1/out/dialogue.wav", "sha256": "a" * 64}
        return svc.finish(s, clip, uuid.UUID(job_id), wav if ok else None, result if ok else None,
                          error=None if ok else "no speech found in the recording")


def test_clip_from_upload_queues_a_media_job(api_as, db, dispatcher):
    creator = api_as("creator")
    up = upload(creator)
    assert creator.post("/api/v1/uploads", params={"kind": "audio", "name": "x.wav"},
                        content=b"not audio at all").status_code == 422
    r = creator.post("/api/v1/audio-clips", json={"upload_id": up["id"], "dialog_text": "Hello!"})
    assert r.status_code == 202, r.text
    clip, job = r.json()["clip"], r.json()["job"]
    assert clip["code"].startswith("AUDIO-") and clip["status"] == "analysing" and clip["job_id"] == job["id"]
    assert job["type"] == "lipsync" and job["queue"] == "media"
    assert job["params"] == {"audio_id": clip["id"], "dialog_text": "Hello!"}
    assert creator.get(f"/api/v1/audio-clips/{clip['code']}").json()["id"] == clip["id"]
    assert creator.get(f"/api/v1/audio-clips/{clip['code']}/dialogue.wav").status_code == 409
    # a BVH upload is not a recording
    bvh = creator.post("/api/v1/uploads", params={"kind": "bvh", "name": "t.bvh"}, content=b"HIERARCHY\n").json()
    assert creator.post("/api/v1/audio-clips", json={"upload_id": bvh["id"]}).json()["code"] == "not_audio"
    # the job's outcome is written once
    assert finish(db, clip["id"], job["id"])["status"] == "ready"
    assert finish(db, clip["id"], job["id"])["recorded"] is False
    got = creator.get(f"/api/v1/audio-clips/{clip['id']}").json()
    assert got["status"] == "ready" and [c["viseme"] for c in got["cues"]] == ["rest", "E", "O", "rest"]
    with db() as s, pytest.raises(Exception, match="it never changes"):
        s.execute(text("UPDATE audio_clips SET cues = '[]' WHERE id = :i"), {"i": clip["id"]})
    with db() as s, pytest.raises(Exception, match="cannot be deleted"):
        s.execute(text("DELETE FROM audio_clips WHERE id = :i"), {"i": clip["id"]})
    # the upload a clip came from stays
    with db() as s, pytest.raises(Exception, match="audio_clips|foreign key"):
        s.execute(text("DELETE FROM uploads WHERE id = :i"), {"i": up["id"]})
        s.commit()


def test_scene_dialogue_freezes_a_ready_clip(api_as, db, cast):  # noqa: F811
    creator = api_as("creator")
    clips = {}
    for key in ("ready", "failed", "pending"):
        up = upload(creator, name=f"{key}.wav")
        r = creator.post("/api/v1/audio-clips", json={"upload_id": up["id"], "dialog_text": "Hello"}).json()
        clips[key] = r["clip"]
        if key != "pending":
            finish(db, r["clip"]["id"], r["job"]["id"], ok=key == "ready", duration=1.0)
    m = manifest(cast["codes"])
    line = {"start_frame": 24, "end_frame": 40, "text": "Hello!", "audio": clips["ready"]["code"]}
    m["instances"][0]["dialogue"] = [line]
    r = create_scene(creator, m)
    assert r.status_code == 201, r.text
    s = r.json()
    v = creator.get(f"/api/v1/scenes/{s['id']}/versions/{s['versions'][0]['id']}").json()
    d = v["resolved"]["instances"][0]["dialogue"][0]
    assert d["audio"]["code"] == clips["ready"]["code"] and d["audio"]["sha256"] == "a" * 64
    assert d["audio"]["path"] == f"audio/{clips['ready']['code']}/dialogue.wav"
    assert [c["viseme"] for c in d["audio"]["cues"]] == ["rest", "E", "O", "rest"]
    # a line shorter than its recording is a warning, the mouth keeps going
    m["instances"][0]["dialogue"] = [{**line, "end_frame": 30}]
    r = creator.post("/api/v1/scenes/validate", json={"manifest": m}).json()
    assert any(i["code"] == "audio_longer_than_line" and i["severity"] == "warning" for i in r["issues"]), r
    # only a ready clip can be used
    for key, code in (("failed", "not_ready"), ("pending", "not_ready")):
        m["instances"][0]["dialogue"] = [{**line, "audio": clips[key]["code"]}]
        r = create_scene(creator, m)
        assert r.status_code == 422 and code in r.text, r.text
    m["instances"][0]["dialogue"] = [{**line, "audio": "AUDIO-999"}]
    assert "not_found" in create_scene(creator, m).text
