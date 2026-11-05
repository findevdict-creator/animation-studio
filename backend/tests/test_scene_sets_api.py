"""Scenes in a set (Phase 7) through the API: the environment and props are pinned like any
asset, placement is checked against the environment version's layout, instances start at
marks, the lighting override is resolved, and the assemble job stages the set's files.

Library assets are published with the same service calls the publisher makes (files made
up); ``blender_engine/tests/test_scene_sets.py`` and the Phase 7 acceptance script build
and reopen set scenes in real Blender."""

import pytest

from tests.test_outfit_library import published_character
from tests.test_scene_library import published_outfit
from tests.test_set_library import env_spec, prop_spec
from tests.test_set_library import published as published_set


@pytest.fixture
def set_cast(api_as, db):
    c, cvid = published_character(api_as, db)
    published_outfit(api_as, db, cvid)
    env, _ = published_set(api_as, db, "environments", env_spec("living_room", marks=[
        {"name": "rug", "at": (-0.6, -0.8), "facing_deg": -20}, {"name": "sofa_seat", "kind": "seat",
                                                                  "item": "sofa_1"}]))
    ball, _ = published_set(api_as, db, "props", prop_spec("ball"))
    table, _ = published_set(api_as, db, "props", prop_spec("table"))
    apple, _ = published_set(api_as, db, "props", prop_spec("apple"))
    draft = api_as("creator").post("/api/v1/props", json={"name": "Draft", "spec": prop_spec("chair")}).json()
    return {"ali": c["code"], "env": env["code"], "ball": ball["code"], "table": table["code"],
            "apple": apple["code"], "draft": draft["code"]}


def pin(code):
    return {"code": code, "version": "1.0"}


def manifest(c, **kw):
    return {"name": "In the living room", "frame_start": 1, "frame_end": 24, "environment": pin(c["env"]),
            "instances": [{"key": "ali", "character": pin(c["ali"]), "mark": "rug",
                           "transform": {"location": (0.1, 0, 0)}},
                          {"key": "ali_2", "character": pin(c["ali"]), "transform": {"location": (1.2, -0.5, 0)}}],
            "props": [{"key": "ball", "prop": pin(c["ball"]), "at": (0.5, -1.5)},
                      {"key": "table", "prop": pin(c["table"]), "at": (2.0, -1.5)},
                      {"key": "apple", "prop": pin(c["apple"]), "at": (2.0, -1.4), "on": "table"},
                      {"key": "apple_2", "prop": pin(c["apple"]), "at": (-0.3, 0.7), "on": "table_1"}],
            **kw}


def create(api, m):
    return api.post("/api/v1/scenes", json={"name": m["name"], "manifest": m})


def test_a_scene_in_a_set_resolves_pins_marks_and_heights(api_as, set_cast):
    r = create(api_as("creator"), manifest(set_cast, lighting="night", camera={"mark": "wide"}))
    assert r.status_code == 201, r.text
    v = r.json()["versions"][0]
    rs = api_as("creator").get(f"/api/v1/scenes/{r.json()['id']}/versions/{v['id']}").json()["resolved"]
    assert rs["environment"]["kind"] == "environment" and rs["environment"]["files"].keys() == {"environment.blend"}
    assert rs["lighting"] == "night" and rs["layout"]["lighting"] == "indoor_day"
    heights = {p["key"]: p["z"] for p in rs["props"]}
    assert heights == {"ball": 0.0, "table": 0.0, "apple": 0.72, "apple_2": 0.4752}
    ali = next(i for i in rs["instances"] if i["key"] == "ali")
    assert ali["transform"]["location"] == [-0.5, -0.8, 0.0] and ali["transform"]["rotation_deg"][2] == -20
    kinds = {p["kind"] for p in (rs["instances"][0]["character"], *(u["pin"] for u in rs["props"]))}
    assert kinds == {"character", "prop"}


def test_placement_errors_are_reported_together(api_as, set_cast):
    c = set_cast
    m = manifest(c, camera={"mark": "drone"})
    m["instances"][1]["transform"] = {"location": (-0.3, 1.8, 0)}  # inside the sofa
    m["instances"][0]["mark"] = "kitchen"
    m["props"] += [{"key": "ball_2", "prop": pin(c["ball"]), "at": (9, 0)},
                   {"key": "table_2", "prop": pin(c["table"]), "at": (-0.3, 1.5)},
                   {"key": "chair", "prop": pin(c["draft"])},
                   {"key": "apple_3", "prop": pin(c["apple"]), "at": (0, -2)}]
    r = create(api_as("creator"), m)
    assert r.status_code == 422
    codes = {(i["path"], i["code"]) for i in r.json()["issues"] if i["severity"] == "error"}
    assert codes == {("camera.mark", "unknown_mark"), ("instances[0].mark", "unknown_mark"),
                     ("instances[1].transform.location", "blocked"), ("props[4].at", "outside"),
                     ("props[5].at", "overlap"), ("props[6].prop", "not_found"),
                     ("props[7].on", "needs_surface")}


def test_seat_marks_warn_and_unpublished_environments_are_refused(api_as, set_cast):
    c = set_cast
    m = manifest(c)
    m["instances"][0]["mark"] = "sofa_seat"
    m["instances"][0]["transform"] = {"location": (0, 0, 0)}
    blocked = create(api_as("creator"), m)  # right in front of the sofa is the table
    assert {i["code"] for i in blocked.json()["issues"]} == {"no_sit_animation", "blocked"}
    m["instances"][0]["transform"] = {"location": (1.2, 0, 0)}
    r = create(api_as("creator"), m)
    assert r.status_code == 201, r.text
    v = r.json()["versions"][0]
    detail = api_as("creator").get(f"/api/v1/scenes/{r.json()['id']}/versions/{v['id']}").json()
    ali = next(i for i in detail["resolved"]["instances"] if i["key"] == "ali")
    x, y, _ = ali["transform"]["location"]
    assert y < 1.8 - 0.425 - 0.2 and abs(x - 0.9) < 1e-6  # in front of the sofa, not on it
    draft_env = api_as("creator").post("/api/v1/environments", json={"name": "Draft park",
                                                                      "spec": env_spec("park")}).json()
    r = create(api_as("creator"), {**manifest(c), "environment": pin(draft_env["code"])})
    assert r.status_code == 422 and {i["code"] for i in r.json()["issues"]} >= {"not_found"}


def test_assemble_stages_the_set(api_as, set_cast, db):
    from app.models.jobs import Job
    from studio_workers.inputs import STAGERS

    r = create(api_as("creator"), manifest(set_cast))
    sid, vid = r.json()["id"], r.json()["versions"][0]["id"]
    r = api_as("creator").post(f"/api/v1/scenes/{sid}/versions/{vid}/assemble", json={})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert job["type"] == "assemble_scene"
    with db() as s:
        params = s.get(Job, job["id"]).params
        wanted = {w[3] for w in STAGERS["assemble_scene"](s, params)}
    env = f"assets/environment/{set_cast['env']}/1.0/environment.blend"
    assert env in wanted and f"assets/prop/{set_cast['apple']}/1.0/prop.blend" in wanted
