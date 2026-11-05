"""Series, episodes, scripts, storyboards, voices, episode versions and render jobs through the
API (Phase 8).

Library assets are published with the publisher's own service calls (files made up), and job
outcomes are recorded with the outcome functions the workers call; ``scripts/phase8_acceptance.py``
runs the same flow with real Blender, speech and FFmpeg jobs."""

import uuid

import pytest
from app.assets import renders as rsvc
from app.jobs import service
from app.models.jobs import Job
from sqlalchemy import text
from studio_contracts.jobs import JobStatus

from tests.test_audio_clips import finish as finish_clip
from tests.test_outfit_library import published_character
from tests.test_scene_library import fake_files, published_outfit
from tests.test_set_library import env_spec
from tests.test_set_library import published as published_set


def pin(code, v="1.0"):
    return {"code": code, "version": v}


@pytest.fixture
def world(api_as, db):
    """Two published characters, each with an outfit approved on it, and a living room."""
    ali, ali_v = published_character(api_as, db)
    maya, maya_v = published_character(api_as, db)
    o1, _ = published_outfit(api_as, db, ali_v)
    o2, _ = published_outfit(api_as, db, maya_v)
    env, env_v = published_set(api_as, db, "environments", env_spec("living_room", marks=[
        {"name": "rug", "at": (-0.6, -0.8)}, {"name": "window", "at": (0.8, -0.6), "facing_deg": 15}]))
    code = lambda path, x: api_as("reviewer").get(f"/api/v1/{path}/{x['id']}").json()["code"]  # noqa: E731
    return {"ali": ali["code"], "maya": maya["code"], "ali_outfit": code("outfits", o1),
            "maya_outfit": code("outfits", o2), "env": env["code"], "env_v": env_v, "ali_v": ali_v,
            "maya_v": maya_v}


def series_body(w, **kw):
    return {"name": "Home Stories", "description": "Short family episodes",
            "settings": {"render": {"width": 640, "height": 360, "samples": 4}},
            "members": [{"character": w["ali"], "key": "ali", "outfit_version": pin(w["ali_outfit"]),
                         "voice": {"voice": "en-us", "variant": "m3", "pitch": 45}},
                        {"character": w["maya"], "key": "maya", "role": "supporting",
                         "outfit_version": pin(w["maya_outfit"]), "voice": {"voice": "en-us", "variant": "f3",
                                                                            "pitch": 70}}],
            **kw}


def script(w):
    return {"title": "The Missing Homework", "synopsis": "Ali can't find his homework; Maya helps.",
            "scenes": [{"scene_id": "SC-001", "location_id": w["env"], "characters": [w["ali"], w["maya"]],
                        "description": "The living room, morning.",
                        "events": [{"character_id": w["ali"], "action": "look_around"},
                                   {"character_id": w["maya"], "action": "wave"}],
                        "dialogue": [{"character_id": w["ali"], "text": "Where is my homework?", "emotion": "sad"},
                                     {"character_id": w["maya"], "text": "Look under the books!",
                                      "emotion": "happy"}]}]}


def run(db, job_id, status=JobStatus.succeeded, error=None):
    with db.begin() as s:
        service.claim(s, uuid.UUID(str(job_id)), "test")
        service.finish(s, uuid.UUID(str(job_id)), status, result={} if status == JobStatus.succeeded else None,
                       error=error)


@pytest.fixture
def episode(api_as, world):
    director = api_as("director")
    s = director.post("/api/v1/series", json=series_body(world))
    assert s.status_code == 201, s.text
    ep = director.post("/api/v1/episodes", json={"series_id": s.json()["code"], "title": "The Missing Homework",
                                                  "concept": "Ali loses his homework"})
    assert ep.status_code == 201, ep.text
    return {"series": s.json(), "episode": ep.json(), **world}


def test_a_series_casts_published_characters_by_identity(api_as, world):
    director = api_as("director")
    assert api_as("creator").post("/api/v1/series", json=series_body(world)).status_code == 403
    r = director.post("/api/v1/series", json=series_body(world))
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["code"].startswith("SERIES-") and [m["key"] for m in s["members"]] == ["ali", "maya"]
    assert s["members"][1]["voice"]["variant"] == "f3" and s["settings"]["render"]["width"] == 640
    assert s["settings"]["render"]["fps"] == 24 and s["style_profile_ref"] == "family_cartoon@1.0"
    bad = director.post("/api/v1/series", json=series_body(world, members=[
        {"character": "CHAR-999", "key": "x"},
        {"character": world["ali"], "key": "ali", "character_version": pin(world["ali"], "7.0")},
        {"character": world["maya"], "key": "ali"}]))
    assert bad.status_code == 422
    assert {(i["path"], i["code"]) for i in bad.json()["issues"]} == {
        ("members[0].character", "not_found"), ("members[1].character_version", "not_published"),
        ("members[2]", "duplicate")}
    # the asset collection holds exact published versions only
    r = director.post(f"/api/v1/series/{s['code']}/assets", json=[world["env_v"]])
    assert r.status_code == 200, r.text
    assert r.json()["assets"], r.text
    assert r.json()["assets"][0]["display"] == f"{world['env']}@1.0"
    assert director.post(f"/api/v1/series/{s['code']}/assets", json=[world["ali_v"]]).status_code == 200


def test_scripts_are_checked_against_the_series_and_versioned(api_as, episode):
    director, ep = api_as("director"), episode["episode"]
    base = f"/api/v1/episodes/{ep['code']}"
    s = script(episode)
    bad = {**s, "scenes": [{**s["scenes"][0], "characters": [episode["ali"], "CHAR-999"], "location_id": "ENV-999"}]}
    r = director.post(f"{base}/scripts/validate", json={"spec": bad}).json()
    assert not r["ok"] and {(i["path"], i["code"]) for i in r["issues"]} == {
        ("scenes[0].characters", "not_found"), ("scenes[0].location_id", "not_found"),
        ("scenes[0].events[1].character_id", "not_in_scene"), ("scenes[0].dialogue[1].character_id", "not_in_scene")}
    assert "cast it in SERIES-" in next(i for i in r["issues"] if i["path"] == "scenes[0].characters")["message"]
    assert director.post(f"{base}/scripts", json={"spec": bad}).status_code == 422
    v1 = director.post(f"{base}/scripts", json={"spec": s})
    assert v1.status_code == 201, v1.text
    assert v1.json()["version"]["number"] == 1 and v1.json()["version"]["source"] == "manual"
    assert v1.json()["version"]["spec"]["episode_id"] == ep["code"]
    edited = {**s, "synopsis": "Ali can't find his homework anywhere."}
    v2 = director.post(f"{base}/scripts", json={"spec": edited, "based_on_id": v1.json()["version"]["id"]}).json()
    assert v2["version"]["number"] == 2 and v2["version"]["based_on_id"] == v1.json()["version"]["id"]
    # an AI source has to point at a script generation log
    r = director.post(f"{base}/scripts", json={"spec": s, "ai_generation_id": str(uuid.uuid4())})
    assert r.status_code == 422 and r.json()["code"] == "not_a_script_generation"
    full = director.get(base).json()
    assert [v["number"] for v in full["script_versions"]] == [1, 2]
    assert api_as("creator").post(f"{base}/scripts", json={"spec": s}).status_code == 403


def make_storyboard(director, episode):
    base = f"/api/v1/episodes/{episode['episode']['code']}"
    sv = director.post(f"{base}/scripts", json={"spec": script(episode)}).json()["version"]
    r = director.post(f"{base}/storyboards", json={"script_version_id": sv["id"]})
    assert r.status_code == 201, r.text
    return sv, r.json()["storyboard"]


def test_rule_storyboard_manual_edits_and_voices(api_as, db, episode, dispatcher):
    director = api_as("director")
    base = f"/api/v1/episodes/{episode['episode']['code']}"
    sv, sb = make_storyboard(director, episode)
    shots = sb["shots"]
    assert [s["shot_id"] for s in shots] == ["SH-001", "SH-002", "SH-003"] and sb["source"] == "rule"
    assert [p["mark"] for p in shots[0]["placement"]] == ["rug", "window"] and shots[0]["camera"]["mark"] == "wide"
    # a hand edit is a new storyboard; a bad one is refused with every problem
    bad = [dict(s) for s in shots]
    bad[1] = {**bad[1], "duration_s": 0.5, "camera": {**bad[1]["camera"], "focus": "CHAR-777"}}
    r = director.post(f"{base}/storyboards", json={"script_version_id": sv["id"], "source": "manual",
                                                   "spec": {"shots": bad}})
    assert r.status_code == 422 and {i["code"] for i in r.json()["issues"]} >= {"too_long", "not_in_shot"}
    edit = [dict(s) for s in shots]
    edit[0] = {**edit[0], "camera": {**edit[0]["camera"], "movement": "push_in"}}
    r = director.post(f"{base}/storyboards", json={"script_version_id": sv["id"], "source": "manual",
                                                   "spec": {"shots": edit}, "based_on_id": sb["id"]})
    assert r.status_code == 201 and r.json()["storyboard"]["number"] == 2
    manual = r.json()["storyboard"]
    # voices: one speech clip per line, with the member's voice
    assert director.post(f"/api/v1/storyboards/{manual['id']}/voiced").json()["code"] == "not_voiced"
    r = director.post(f"/api/v1/storyboards/{manual['id']}/voice")
    assert r.status_code == 202, r.text
    lines = r.json()["lines"]
    assert len(lines) == 2 and all(ln["new"] for ln in lines) and len(r.json()["jobs"]) == 2
    job = r.json()["jobs"][1]
    assert job["type"] == "synthesize_speech" and job["queue"] == "media"
    assert job["params"]["voice"]["variant"] == "f3" and job["params"]["text"] == "Look under the books!"
    clip = api_as("reviewer").get(f"/api/v1/audio-clips/{lines[1]['audio']}").json()
    assert clip["source"] == "tts" and clip["voice"]["pitch"] == 70 and clip["upload_id"] is None
    # asking again reuses the clips in progress
    again = director.post(f"/api/v1/storyboards/{manual['id']}/voice").json()
    assert [ln["audio"] for ln in again["lines"]] == [ln["audio"] for ln in lines] and again["jobs"] == []
    for ln, dur in zip(lines, (1.2, 3.4), strict=True):
        assert finish_clip(db, clip_id(api_as, ln["audio"]), str(ln["job_id"]), duration=dur)["status"] == "ready"
    r = director.post(f"/api/v1/storyboards/{manual['id']}/voiced")
    assert r.status_code == 201, r.text
    voiced = r.json()["storyboard"]
    assert voiced["source"] == "voiced" and voiced["based_on_id"] == manual["id"]
    assert voiced["shots"][2]["dialogue"][0]["audio"] == lines[1]["audio"]
    assert voiced["shots"][2]["duration_s"] == pytest.approx(0.3 + 3.4 + 0.4)  # lengthened to fit
    assert voiced["shots"][1]["duration_s"] == shots[1]["duration_s"]  # it already fitted
    assert voiced["shots"][0]["camera"]["movement"] == "push_in"


def clip_id(api_as, code):
    return api_as("reviewer").get(f"/api/v1/audio-clips/{code}").json()["id"]


def voiced_storyboard(api_as, db, episode):
    director = api_as("director")
    _, sb = make_storyboard(director, episode)
    r = director.post(f"/api/v1/storyboards/{sb['id']}/voice").json()
    for ln in r["lines"]:
        if ln["new"]:
            finish_clip(db, clip_id(api_as, ln["audio"]), str(ln["job_id"]), duration=1.0)
    return director.post(f"/api/v1/storyboards/{sb['id']}/voiced").json()["storyboard"]


def build_all(api_as, db, v):
    """Build every shot (the assemble outcome recorded with made-up files)."""
    from app.assets import scenes as scene_svc
    from app.models.scenes import SceneVersion

    r = api_as("director").post(f"/api/v1/episode-versions/{v['id']}/build", json={})
    assert r.status_code == 202, r.text
    for job in r.json()["jobs"]:
        vid = uuid.UUID(job["params"]["scene_version_id"])
        with db.begin() as s:
            digest = s.get(SceneVersion, vid).manifest_digest
            out = scene_svc.record_build(s, uuid.UUID(job["id"]), vid,
                                         fake_files(job["id"], ["scene.blend", "scene_report.json"]),
                                         {"ok": True, "scene_blend_sha256": "a" * 64,
                                          "report": {"manifest_digest": digest, "ok": True}}, "5.2.3")
            assert out["build_status"] == "built"
    return r.json()


def test_an_episode_version_compiles_shots_into_pinned_scenes(api_as, db, episode, dispatcher):
    director = api_as("director")
    base = f"/api/v1/episodes/{episode['episode']['code']}"
    sb = voiced_storyboard(api_as, db, episode)
    r = director.post(f"{base}/versions", json={"storyboard_id": sb["id"], "notes": "first cut"})
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["status"] == "draft" and v["new_scene_versions"] == 3 and len(v["shots"]) == 3
    m = v["manifest"]
    assert [c["key"] for c in m["cast"]] == ["ali", "maya"] and m["cast"][0]["outfit"] == pin(episode["ali_outfit"])
    assert m["cast"][1]["voice"]["variant"] == "f3" and m["environments"] == [pin(episode["env"])]
    assert m["render"]["width"] == 640 and [s["shot_id"] for s in m["shots"]] == ["SH-001", "SH-002", "SH-003"]
    assert {c["display"] for c in v["closure"]} == {f"{episode['ali']}@1.0", f"{episode['maya']}@1.0",
                                                     f"{episode['ali_outfit']}@1.0", f"{episode['maya_outfit']}@1.0",
                                                     f"{episode['env']}@1.0"}
    assert sorted(a["role"] for a in v["audio"]) == ["dialogue", "dialogue"]
    starts = [s["start_s"] for s in v["shots"]]
    assert starts[0] == 0 and starts[1] == pytest.approx(v["shots"][0]["duration_s"])
    assert v["duration_s"] == pytest.approx(sum(s["duration_s"] for s in v["shots"]))
    # the scene behind a line has the speaker's recording, expression and the camera on them
    sc = v["shots"][2]
    scene = director.get(f"/api/v1/scenes/{sc['scene_id']}/versions/{sc['scene_version_id']}").json()
    maya = next(i for i in scene["resolved"]["instances"] if i["key"] == "maya")
    assert maya["dialogue"][0]["audio"]["code"] == v["audio"][1]["code"]
    assert scene["resolved"]["camera"]["focus"] == "maya" and scene["resolved"]["environment"]["code"] == episode["env"]
    assert [e["expression"] for e in maya["expressions"]][-1] == "happy"
    assert director.get(f"{base}/scenes").json()[2]["scene_code"] == sc["scene_code"]
    # the same storyboard again reuses every scene version; a changed render setting is not a scene change
    again = director.post(f"{base}/versions", json={"storyboard_id": sb["id"], "render": {"samples": 2}}).json()
    assert again["new_scene_versions"] == 0 and again["number"] == 2
    assert [s["scene_version_id"] for s in again["shots"]] == [s["scene_version_id"] for s in v["shots"]]
    # problems in any shot are reported together
    bad = director.post(f"{base}/versions", json={"storyboard_id": sb["id"], "environments": [pin(episode["env"], "9.9")],
                                                  "cast": [{"character": episode["ali"],
                                                            "outfit": pin(episode["maya_outfit"])}]})
    assert bad.status_code == 422
    codes = {(i["path"].split(".")[0], i["code"]) for i in bad.json()["issues"]}
    assert ("shots[0]", "fit_not_approved") in codes and any(c == "not_found" for _, c in codes)


def test_lock_build_render_and_the_guards(api_as, db, episode, dispatcher):
    director = api_as("director")
    base = f"/api/v1/episodes/{episode['episode']['code']}"
    sb = voiced_storyboard(api_as, db, episode)
    v = director.post(f"{base}/versions", json={"storyboard_id": sb["id"]}).json()
    vbase = f"/api/v1/episode-versions/{v['id']}"
    assert director.post(f"{vbase}/lock").json()["code"] == "not_built"
    assert director.post("/api/v1/render-jobs", json={"episode_version_id": v["id"]}).json()["code"] == "not_locked"
    built = build_all(api_as, db, v)
    assert len(built["jobs"]) == 3 and {j["type"] for j in built["jobs"]} == {"assemble_scene"}
    assert director.post(f"{vbase}/build", json={}).json()["jobs"] == []  # nothing left to build
    reopen = director.post(f"{vbase}/reopen", json={}).json()
    assert {j["type"] for j in reopen["jobs"]} == {"verify_scene"}
    locked = director.post(f"{vbase}/lock")
    assert locked.status_code == 200 and locked.json()["status"] == "locked", locked.text
    assert director.post(f"{vbase}/lock").json()["code"] == "already_locked"
    vid = uuid.UUID(v["id"])
    with db() as s, pytest.raises(Exception, match="locked, it never changes"):
        s.execute(text("UPDATE episode_versions SET notes = 'x' WHERE id = :i"), {"i": vid})
    with db() as s, pytest.raises(Exception, match="locked, it cannot be deleted"):
        s.execute(text("DELETE FROM episode_versions WHERE id = :i"), {"i": vid})
    with db() as s, pytest.raises(Exception, match="are frozen"):
        s.execute(text("DELETE FROM episode_assets WHERE episode_version_id = :i"), {"i": vid})
    with db() as s, pytest.raises(Exception, match="are frozen"):
        s.execute(text("UPDATE episode_version_shots SET start_s = 9 WHERE episode_version_id = :i"), {"i": vid})
    with db() as s, pytest.raises(Exception, match="append-only"):
        s.execute(text("UPDATE shots SET duration_s = 9 WHERE storyboard_id = :i"), {"i": uuid.UUID(sb["id"])})
    with db() as s, pytest.raises(Exception, match="append-only"):
        s.execute(text("DELETE FROM script_versions"))

    # a render: one Blender job per shot with the settings in force and the exact pins
    assert api_as("creator").post("/api/v1/render-jobs", json={"episode_version_id": v["id"]}).status_code == 403
    assert director.post("/api/v1/render-jobs", json={"episode_version_id": v["id"],
                                                       "settings": {"fps": 30}}).json()["code"] == "fps_mismatch"
    r = director.post("/api/v1/render-jobs", json={"episode_version_id": v["id"], "settings": {"samples": 2}})
    assert r.status_code == 202, r.text
    rj = r.json()
    assert rj["code"].startswith("RENDER-") and rj["status"] == "queued" and rj["settings"]["samples"] == 2
    assert rj["settings"]["width"] == 640 and sorted(rj["asset_versions"]) == sorted(c["display"] for c in v["closure"])
    jobs = [s["job"] for s in rj["shots"]]
    assert {j["type"] for j in jobs} == {"render_scene"} and {j["queue"] for j in jobs} == {"render"}
    assert jobs[0]["params"]["scene_blend_sha256"] == "a" * 64 and jobs[0]["params"]["settings"]["samples"] == 2
    from studio_workers.inputs import STAGERS
    with db() as s:
        wanted = {w[3] for w in STAGERS["render_scene"](s, s.get(Job, uuid.UUID(jobs[0]["id"])).params)}
    assert "scene/scene.blend" in wanted and f"assets/environment/{episode['env']}/1.0/environment.blend" in wanted
    draft = director.post(f"{base}/versions", json={"storyboard_id": sb["id"]}).json()
    assert draft["status"] == "draft"
    with db() as s, pytest.raises(Exception, match="only a locked version"):
        s.execute(text("INSERT INTO render_jobs (id, code, episode_id, episode_version_id, status, settings, "
                       "asset_versions, cancel_requested) SELECT gen_random_uuid(), 'RENDER-X', episode_id, id, "
                       "'queued', '{}', '[]', false FROM episode_versions WHERE id = :i"), {"i": uuid.UUID(draft["id"])})

    # shot outcomes; the last one queues the export
    for i, j in enumerate(jobs):
        frames = [{"path": f"frames/frame_{f:04d}.png", "role": "frame", "mime_type": "image/png",
                   "storage_key": f"jobs/{j['id']}/attempt-1/out/frames/frame_{f:04d}.png", "sha256": "e" * 64,
                   "size_bytes": 100} for f in (1, 2, 3)]
        with db.begin() as s:
            service.claim(s, uuid.UUID(j["id"]), "test")
            out = rsvc.record_shot(s, uuid.UUID(j["id"]), frames, {"seconds_per_frame": 1.5}, "5.2.3")
            service.finish(s, uuid.UUID(j["id"]), JobStatus.succeeded, result={})
        assert out["recorded"] and out["frames"] == 3
        assert ("_dispatch" in out) == (i == len(jobs) - 1)
        if i == 0:
            mid = director.get(f"/api/v1/render-jobs/{rj['code']}").json()
            assert mid["status"] == "rendering" and 0 < mid["progress"]["value"] < 0.9
    after = director.get(f"/api/v1/render-jobs/{rj['code']}").json()
    assert after["status"] == "exporting" and after["export_job"]["type"] == "export_episode"
    assert after["blender_version"] == "5.2.3" and [o["frame_count"] for o in after["outputs"]] == [3, 3, 3]
    with db() as s:
        plan = rsvc.export_plan(s, rsvc.get(s, rj["code"]))
    assert [sh["shot_id"] for sh in plan["shots"]] == ["SH-001", "SH-002", "SH-003"]
    assert len(plan["shots"][1]["frames"]) == 3 and plan["width"] == 640 and plan["fps"] == 24
    lines = [a for a in plan["audio"] if a["role"] == "dialogue"]
    assert len(lines) == 2 and all(a["audio"] in plan["clips"] for a in lines)
    assert lines[0]["start_s"] == pytest.approx(plan["shots"][1]["start_s"] + round(0.3 * 24) / 24, abs=1e-3)
    ex = after["export_job"]["id"]
    with db.begin() as s:
        service.claim(s, uuid.UUID(ex), "test")
        rsvc.record_export(s, uuid.UUID(ex), uuid.UUID(rj["id"]),
                           {"storage_key": f"jobs/{ex}/attempt-1/out/episode.mp4", "sha256": "f" * 64, "size_bytes": 9},
                           {"duration_s": plan["duration_s"]}, [])
        service.finish(s, uuid.UUID(ex), JobStatus.succeeded, result={})
    done = director.get(f"/api/v1/render-jobs/{rj['code']}").json()
    assert done["status"] == "completed" and done["video_url"] and done["finished_at"]
    assert director.get(f"/api/v1/render-jobs/{rj['code']}/video", follow_redirects=False).status_code == 302
    assert len(director.get(f"/api/v1/render-jobs/{rj['code']}/logs").json()) == 4
    shot_frames = next(o for o in done["outputs"] if o["kind"] == "frames" and o["shot_id"] == "SH-002")
    one = f"/api/v1/render-jobs/{rj['code']}/outputs/{shot_frames['id']}"
    assert director.get(one).json()["code"] == "frame_required"
    assert director.get(f"{one}?frame=4").json()["code"] == "frame_required"
    url = director.get(f"{one}?frame=2&redirect=false").json()["url"]
    assert "frame_0002.png" in url and f"{rj['code']}-SH-002-0002.png" in url
    assert director.get(f"/api/v1/render-jobs/{rj['code']}/outputs/{uuid.uuid4()}").status_code == 404
    assert director.post(f"/api/v1/render-jobs/{rj['code']}/cancel").json()["code"] == "render_not_cancellable"
    with db() as s, pytest.raises(Exception, match="kept as a record"):
        s.execute(text("DELETE FROM render_jobs WHERE code = :c"), {"c": rj["code"]})
    with db() as s, pytest.raises(Exception, match="keeps its episode version"):
        s.execute(text("UPDATE render_jobs SET settings = '{}' WHERE code = :c"), {"c": rj["code"]})


def test_render_cancel_and_retry(api_as, db, episode, dispatcher):
    director = api_as("director")
    base = f"/api/v1/episodes/{episode['episode']['code']}"
    sb = voiced_storyboard(api_as, db, episode)
    v = director.post(f"{base}/versions", json={"storyboard_id": sb["id"]}).json()
    build_all(api_as, db, v)
    assert director.post(f"/api/v1/episode-versions/{v['id']}/lock").status_code == 200
    rj = director.post("/api/v1/render-jobs", json={"episode_version_id": v["id"]}).json()
    jobs = [s["job"]["id"] for s in rj["shots"]]
    run(db, jobs[0], JobStatus.failed, {"message": "Blender crashed"})
    failed = director.get(f"/api/v1/render-jobs/{rj['code']}").json()
    assert failed["status"] == "failed" and failed["error"]["message"] == "Blender crashed"
    assert failed["error"]["job_id"] == jobs[0]
    r = director.post(f"/api/v1/render-jobs/{rj['code']}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued" and r.json()["error"] is None  # every shot job is waiting again
    assert r.json()["shots"][0]["job"]["status"] == "queued"
    assert director.post(f"/api/v1/render-jobs/{rj['code']}/retry").json()["code"] == "render_not_retryable"
    c = director.post(f"/api/v1/render-jobs/{rj['code']}/cancel")
    assert c.status_code == 200 and c.json()["status"] == "cancelled" and c.json()["cancel_requested"]
    assert {s["job"]["status"] for s in c.json()["shots"]} == {"cancelled"}


# ---------------------------------------------------------------- AI suggestions (fake provider)
def script_proposal(w):
    return {"title": "The Missing Homework", "synopsis": "Ali looks for his homework.",
            "scenes": [{"location": w["env"], "description": "Morning", "characters": [w["ali"], w["maya"]],
                        "events": [{"character": w["ali"], "action": "look_around"}],
                        "dialogue": [{"character": w["ali"], "text": "Where is it?", "emotion": "sad"},
                                     {"character": w["maya"], "text": "Under the books!", "emotion": "happy"}]}],
            "notes": "two lines"}


def test_ai_script_and_shot_suggestions_are_validated_logged_and_kept_by_a_person(api_as, db, episode, dispatcher,
                                                                                 storage_as):
    from app.ai.providers import LLMSettings
    from app.ai.tasks import episode_ai as T
    from app.models.ai import AIGeneration
    from studio_testing.llm_fakes import FakeResponse, fake_client_for
    from studio_workers.ai_runner import AIRunner

    director = api_as("director")
    ep = episode["episode"]
    base = f"/api/v1/episodes/{ep['code']}"
    with db() as s:
        ctx = T.load_context(s, uuid.UUID(ep["id"]))
    assert [c.code for c in ctx.cast] == [episode["ali"], episode["maya"]]
    smodel = T.script_model(ctx)
    with pytest.raises(ValueError):  # names and unknown codes are not in the schema
        smodel.model_validate({**script_proposal(episode), "scenes": [{**script_proposal(episode)["scenes"][0],
                                                                       "characters": ["Ali"]}]})
    # the first reply makes Maya speak in a scene she is not in; the repair turn fixes it
    bad = script_proposal(episode)
    bad["scenes"][0] = {**bad["scenes"][0], "characters": [episode["ali"]]}
    llm = LLMSettings(clients={"anthropic": fake_client_for("anthropic", FakeResponse(smodel.model_validate(bad)),
                                                            FakeResponse(smodel.model_validate(script_proposal(episode))))})
    r = director.post(f"{base}/scripts/generate", json={"target_duration_s": 20})
    assert r.status_code == 202 and r.json()["queue"] == "ai", r.text
    out = AIRunner(llm=llm, work_storage=storage_as("worker", "work"), dispatcher=dispatcher).run(uuid.UUID(r.json()["id"]))
    assert out.status.value == "succeeded"
    data = director.get(f"/api/v1/jobs/{r.json()['id']}").json()["result"]["data"]
    assert data["valid"] and data["spec"]["scenes"][0]["scene_id"] == "SC-001"
    assert data["spec"]["episode_id"] == ep["code"]
    with db() as s:
        gen = s.get(AIGeneration, uuid.UUID(data["ai_generation_id"]))
        assert gen.task == "generate_script" and gen.repair_turns == 1 and gen.valid
    # nothing was saved; a person keeps it (here as is) as a script version
    assert director.get(base).json()["script_versions"] == []
    saved = director.post(f"{base}/scripts", json={"spec": data["spec"], "ai_generation_id": data["ai_generation_id"]})
    assert saved.status_code == 201 and saved.json()["version"]["source"] == "ai", saved.text
    sv = saved.json()["version"]

    # shots: lines are referred to by index and their text is copied from the script
    with db() as s:
        ctx = T.load_context(s, uuid.UUID(ep["id"]), uuid.UUID(sv["id"]))
    shmodel = T.shots_model(ctx)
    proposal = {"shots": [
        {"scene": "SC-001", "camera": "wide", "camera_mark": "wide", "movement": "push_in", "duration_s": 3,
         "placement": [{"character": episode["ali"], "mark": "rug"}, {"character": episode["maya"], "mark": "window"}],
         "actions": [{"character": episode["ali"], "action": "look_around"}]},
        {"scene": "SC-001", "camera": "close", "focus": episode["ali"], "lines": [0], "duration_s": 1.5,
         "placement": [{"character": episode["ali"], "mark": "rug"}]},
        {"scene": "SC-001", "camera": "medium", "focus": episode["maya"], "lines": [1], "duration_s": 2,
         "placement": [{"character": episode["maya"], "mark": "window"}]}]}
    missing = {"shots": proposal["shots"][:2]}
    llm = LLMSettings(clients={"anthropic": fake_client_for("anthropic", FakeResponse(shmodel.model_validate(missing)),
                                                            FakeResponse(shmodel.model_validate(proposal)))})
    r = director.post(f"{base}/storyboards/suggest", json={"script_version_id": sv["id"]})
    assert r.status_code == 202, r.text
    AIRunner(llm=llm, work_storage=storage_as("worker", "work"), dispatcher=dispatcher).run(uuid.UUID(r.json()["id"]))
    job = director.get(f"/api/v1/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job["error"]
    data = job["result"]["data"]
    shots = data["spec"]["shots"]
    assert [s["shot_id"] for s in shots] == ["SH-001", "SH-002", "SH-003"]
    assert shots[1]["dialogue"][0]["text"] == "Where is it?" and shots[1]["placement"][0]["emotion"] == "sad"
    assert shots[0]["environment"] == episode["env"] and shots[0]["camera"]["movement"] == "push_in"
    # an AI storyboard needs its generation; then it is saved like any other
    assert director.post(f"{base}/storyboards", json={"script_version_id": sv["id"], "source": "ai",
                                                      "spec": data["spec"]}).json()["code"] == "not_a_shot_suggestion"
    r = director.post(f"{base}/storyboards", json={"script_version_id": sv["id"], "source": "ai", "spec": data["spec"],
                                                   "ai_generation_id": data["ai_generation_id"]})
    assert r.status_code == 201 and r.json()["storyboard"]["source"] == "ai", r.text


def test_an_ai_suggestion_that_stays_invalid_fails_and_is_logged(api_as, db, episode, dispatcher, storage_as):
    from app.ai.providers import LLMSettings
    from app.ai.tasks import episode_ai as T
    from app.models.ai import AIGeneration
    from studio_testing.llm_fakes import FakeResponse, fake_client_for
    from studio_workers.ai_runner import AIRunner

    ep = episode["episode"]
    with db() as s:
        ctx = T.load_context(s, uuid.UUID(ep["id"]))
    bad = script_proposal(episode)
    bad["scenes"][0] = {**bad["scenes"][0], "characters": [episode["ali"]]}
    llm = LLMSettings(clients={"anthropic": fake_client_for("anthropic",
                                                            FakeResponse(T.script_model(ctx).model_validate(bad)))})
    job = api_as("director").post(f"/api/v1/episodes/{ep['code']}/scripts/generate", json={}).json()
    AIRunner(llm=llm, work_storage=storage_as("worker", "work"), dispatcher=dispatcher).run(uuid.UUID(job["id"]))
    with db() as s:
        row = s.get(Job, uuid.UUID(job["id"]))
        assert row.status == "failed" and "not_in_scene" in row.error["message"]
        assert s.query(AIGeneration).filter_by(job_id=row.id, valid=False, task="generate_script").count() == 1
