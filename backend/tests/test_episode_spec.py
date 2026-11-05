"""Series, script, storyboard and episode contracts (Phase 8): pure validation, the rule-based
storyboard plan, and compiling a shot into a scene manifest the Phase 5 to 7 machinery
accepts."""

import pytest
from pydantic import ValidationError
from studio_contracts.episode import (
    ACTION_CLIPS,
    EpisodeManifest,
    RenderSettings,
    ScriptSpecification,
    StoryboardSpecification,
    line_seconds,
    plan_storyboard,
    shot_to_scene,
    validate_episode,
    validate_script,
    validate_storyboard,
)
from studio_contracts.jobs import (
    JOB_PARAMS,
    JOB_QUEUES,
    JobType,
    Queue,
    RenderSceneParams,
)
from studio_contracts.scene import SceneManifest, validate_manifest

SCRIPT = {
    "title": "The Missing Homework",
    "synopsis": "Ali can't find his homework; Maya helps him look.",
    "scenes": [
        {"scene_id": "SC-001", "location_id": "ENV-001", "characters": ["CHAR-001", "CHAR-002"],
         "description": "The living room, morning.",
         "events": [{"character_id": "CHAR-001", "action": "look_around", "emotion": "confused"},
                    {"character_id": "CHAR-002", "action": "wave"}],
         "dialogue": [{"character_id": "CHAR-001", "text": "Where is my homework?", "emotion": "confused"},
                      {"character_id": "CHAR-002", "text": "Did you look under the books?"},
                      {"character_id": "CHAR-002", "text": "It was there yesterday!", "emotion": "excited"}]},
        {"scene_id": "SC-002", "location_id": "ENV-001", "characters": ["CHAR-001"],
         "dialogue": [{"character_id": "CHAR-001", "text": "Found it!", "emotion": "happy"}]},
    ],
}


def pin(code, v="1.0"):
    return {"code": code, "version": v}


EPISODE = {
    "title": "The Missing Homework",
    "cast": [{"key": "ali", "character": pin("CHAR-001"), "outfit": pin("OUTFIT-001")},
             {"key": "maya", "character": pin("CHAR-002"), "outfit": pin("OUTFIT-002"),
              "voice": {"voice": "en-us", "variant": "f3", "pitch": 70}}],
    "environments": [pin("ENV-001")],
    "action_animations": {"walk": "ANIM-011@1.0"},
}


def test_a_script_validates_its_own_references_and_the_registry_codes():
    s = ScriptSpecification.model_validate(SCRIPT)
    assert validate_script(s, {"CHAR-001", "CHAR-002"}, {"ENV-001"}) == []
    assert s.characters() == ["CHAR-001", "CHAR-002"] and s.locations() == ["ENV-001"]
    bad = ScriptSpecification.model_validate({**SCRIPT, "scenes": [
        {**SCRIPT["scenes"][0], "events": [{"character_id": "CHAR-009", "action": "juggle"}],
         "dialogue": [{"character_id": "CHAR-001", "text": "Hm.", "emotion": "grumpy"}]},
        {**SCRIPT["scenes"][1], "scene_id": "SC-001", "location_id": "ENV-404"}]})
    codes = {(i.path, i.code, i.severity) for i in validate_script(bad, {"CHAR-001", "CHAR-002"}, {"ENV-001"})}
    assert codes == {("scenes[0].events[0].character_id", "not_in_scene", "error"),
                     ("scenes[0].events[0].action", "no_clip", "warning"),
                     ("scenes[0].dialogue[0].emotion", "unknown_expression", "error"),
                     ("scenes[1].scene_id", "duplicate", "error"),
                     ("scenes[1].location_id", "not_found", "error")}
    with pytest.raises(ValidationError):  # names are not references
        ScriptSpecification.model_validate({**SCRIPT, "scenes": [{**SCRIPT["scenes"][0], "characters": ["Ali"]}]})


def test_the_rule_based_plan_gives_an_establishing_shot_and_a_shot_per_line():
    s = ScriptSpecification.model_validate(SCRIPT)
    sb = plan_storyboard(s, marks={"ENV-001": ["rug", "window"]})
    assert [x.shot_id for x in sb.shots] == ["SH-001", "SH-002", "SH-003", "SH-004", "SH-005", "SH-006"]
    est, l1, l2, l3, est2, l4 = sb.shots
    assert est.camera.type == "wide" and est.camera.mark == "wide" and len(est.actions) == 2
    assert [p.mark for p in est.placement] == ["rug", "window"]
    assert (l1.camera.type, l1.camera.focus) == ("medium", "CHAR-001")
    assert (l2.camera.type, l3.camera.type) == ("medium", "close")  # Maya twice in a row
    assert l3.placement[1].emotion == "excited" and l3.placement[0].emotion is None
    assert est2.transition_in.kind == "fade" and est.transition_in.kind == "cut"
    assert l1.duration_s == pytest.approx(line_seconds("Where is my homework?") + 0.8)
    assert validate_storyboard(sb, s) == []


def test_storyboard_problems_are_reported_together():
    s = ScriptSpecification.model_validate(SCRIPT)
    sb = plan_storyboard(s, marks={"ENV-001": ["rug", "window"]}).model_dump(mode="json")
    sb["shots"][1]["duration_s"] = 1.0  # the line does not fit
    sb["shots"][1]["camera"]["focus"] = "CHAR-003"
    sb["shots"][0]["actions"].append({"character_id": "CHAR-002", "action": "wave", "start_s": 9.0,
                                      "animation": "CLIP-JUGGLE@0.1"})
    sb["shots"][2]["lighting"] = "disco"
    sb["shots"][4]["scene_id"] = "SC-009"
    sb["shots"][5]["shot_id"] = "SH-001"
    sb["shots"][5]["environment"] = None
    issues = validate_storyboard(StoryboardSpecification.model_validate(sb), s)
    assert {(i.path, i.code) for i in issues} == {
        ("shots[1].dialogue[0]", "too_long"), ("shots[1].camera.focus", "not_in_shot"),
        ("shots[0].actions[2].start_s", "out_of_range"), ("shots[0].actions[2].animation", "unknown_clip"),
        ("shots[2].lighting", "unknown_lighting"), ("shots[4].scene_id", "not_in_script"),
        ("shots[5].shot_id", "duplicate"), ("shots[5].placement[0].mark", "no_environment")}


def test_a_shot_compiles_into_a_valid_scene_manifest_with_the_cast_pins():
    s = ScriptSpecification.model_validate(SCRIPT)
    sb = plan_storyboard(s, marks={"ENV-001": ["rug", "window"]})
    ep = EpisodeManifest.model_validate(EPISODE)
    assert validate_episode(ep) == []
    est = sb.shots[0].model_copy(update={"actions": [*sb.shots[0].actions,
                                                       sb.shots[0].actions[0].model_copy(update={"action": "walk"}),
                                                       sb.shots[0].actions[0].model_copy(update={"action": "juggle"})]})
    m, issues = shot_to_scene(est, ep)
    assert [i.code for i in issues] == ["no_clip"]
    assert validate_manifest(m) == []
    assert m.environment.ref == "ENV-001@1.0" and m.camera.mark == "wide" and m.frame_end == round(est.duration_s * 24)
    ali, maya = m.instances
    assert (ali.key, ali.character.ref, ali.outfit.ref, ali.mark) == ("ali", "CHAR-001@1.0", "OUTFIT-001@1.0", "rug")
    assert [a.clip for a in ali.animations] == ["look_around", "ANIM-011@1.0"]
    assert [a.clip for a in maya.animations] == ["wave"]
    line = sb.shots[2]  # Maya speaks, framed medium on her
    m2, issues = shot_to_scene(line, ep, audio_s={})
    assert issues == [] and validate_manifest(m2) == []
    assert m2.camera.focus == "maya" and m2.camera.preset == "medium"
    d = m2.instances[1].dialogue[0]
    assert d.start_frame == 1 + round(0.3 * 24) and d.text == "Did you look under the books?"
    assert m2.instances[0].animations[0].clip == "idle"  # Ali listens
    timed = line.model_copy(update={"dialogue": [line.dialogue[0].model_copy(update={"audio": "AUDIO-007"})]})
    m3, _ = shot_to_scene(timed, ep, audio_s={"AUDIO-007": 1.5})
    d3 = m3.instances[1].dialogue[0]
    assert d3.audio == "AUDIO-007" and d3.end_frame - d3.start_frame == round(1.5 * 24)
    m4, issues = shot_to_scene(line, EpisodeManifest.model_validate({**EPISODE, "cast": EPISODE["cast"][:1],
                                                                       "environments": []}))
    assert {i.code for i in issues} == {"not_cast"} and m4.environment is None and m4.instances[0].mark is None
    assert SceneManifest.model_validate(m.model_dump(mode="json")).digest() == m.digest()


def test_episode_checks_and_render_settings():
    bad = EpisodeManifest.model_validate({**EPISODE, "cast": [*EPISODE["cast"], EPISODE["cast"][0]],
                                          "action_animations": {"juggle": "CLIP-JUGGLE@0.1"},
                                          "environments": [pin("ENV-001"), pin("ENV-001", "1.1")],
                                          "render": {"width": 1921}})
    assert {i.code for i in validate_episode(bad)} == {"duplicate", "unknown_clip", "odd_size"}
    r = RenderSettings()
    assert (r.width, r.height, r.fps, r.frame_format, r.video_codec, r.audio_codec) == (1920, 1080, 24, "PNG", "h264", "aac")
    assert set(ACTION_CLIPS.values()) <= {"idle", "wave", "walk", "nod", "point", "look_around"}


def test_phase8_job_types():
    assert JOB_QUEUES[JobType.render_scene] == Queue.render and JOB_QUEUES[JobType.export_episode] == Queue.media
    assert JOB_QUEUES[JobType.generate_script] == Queue.ai and JOB_QUEUES[JobType.synthesize_speech] == Queue.media
    assert set(JOB_PARAMS) == set(JobType)
    with pytest.raises(ValidationError):
        RenderSceneParams.model_validate({"render_job_id": "0" * 32, "scene_version_id": "0" * 32, "resolved": {},
                                          "scene_blend_sha256": "0" * 64, "settings": {}})
