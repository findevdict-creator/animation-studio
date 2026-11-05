"""Environment and prop contracts (SRS 12, Phase 7): catalogs, layout validation,
resolution, and the scene manifest's set (environment, props, marks, camera marks)."""

import pytest
from pydantic import ValidationError
from studio_contracts.environment import (
    ENVIRONMENTS,
    LIGHTING,
    PROPS,
    EnvironmentSpecification,
    InvalidSpecification,
    PropSpecification,
    get_prop_def,
    overlap,
    resolve_environment,
    resolve_prop,
    validate_environment,
    validate_prop,
)
from studio_contracts.jobs import GenerateEnvironmentParams, GeneratePropParams
from studio_contracts.scene import (
    PropFacts,
    SceneManifest,
    check_layout,
    start_transform,
    validate_manifest,
)


def errors(issues):
    return {(i.path, i.code) for i in issues if i.severity == "error"}


def env(template="living_room", **kw):
    t = next(e for e in ENVIRONMENTS if e.key == template)
    return EnvironmentSpecification.model_validate({"name": "Home", "template": {"id": t.id, "version": t.version},
                                                    **kw})


def item(key):
    p = get_prop_def(key)
    return {"id": p.id, "version": p.version}


def test_every_template_validates_and_resolves_with_its_own_dressing():
    for t in ENVIRONMENTS:
        spec = env(t.key)
        assert errors(validate_environment(spec)) == set(), t.key
        r = resolve_environment(spec)
        assert len(r.dressing) == len(t.dressing)
        assert {c.name for c in r.cameras} == {"wide", "front"}
        assert r.mark("centre") is not None and r.walkable(0, 0)
        if t.kind == "room":
            assert r.mark("door") is not None and r.walkable(*r.mark("door").at[:2])
            assert r.wall_color and any(o.kind == "door" for o in r.openings)
        else:
            assert r.wall_finish is None and r.openings == ()
        assert r.digest() == resolve_environment(spec).digest()


def test_every_prop_resolves_and_scales():
    for p in PROPS:
        r = resolve_prop(PropSpecification(name=p.key, item={"id": p.id, "version": p.version}))
        assert r.size == p.base_size and r.color.startswith("#")
    big = resolve_prop(PropSpecification(name="t", item=item("table"), params={"size": 1.0, "height": 1.0}))
    assert big.size == (1.44, 0.9, 1.08) and big.surface_z("top") == 1.08
    issues = validate_prop(PropSpecification(name="t", item=item("table"), params={"size": 2, "legs": 3},
                                             material={"finish": "rubber", "color": "mauve-ish"}))
    assert errors(issues) == {("params.size", "out_of_range"), ("params.legs", "unknown_param"),
                              ("material.finish", "not_for_item"), ("material.color", "unknown_color")}
    with pytest.raises(InvalidSpecification):
        resolve_prop(PropSpecification(name="t", item={"id": "PROPD-ROCKET", "version": "0.1"}))
    with pytest.raises(ValidationError):  # the job refuses an invalid spec before it is queued
        GeneratePropParams(spec={"name": "t", "item": item("table"), "params": {"size": 9}})


def test_overlap_handles_rotation():
    a = (0, 0, 2, 0.4, 0)
    assert overlap(a, (0, 0.5, 2, 0.4, 0)) is False
    assert overlap(a, (1.2, 0.5, 2, 0.4, 90)) is True  # rotated, it reaches down into a
    assert overlap(a, (0, 0.41, 2, 0.4, 0), gap=0.02) is True


def test_layout_errors_are_reported_together():
    spec = env(size=(5, 4, 2.6), lighting="disco", materials={"floor_finish": "grass"},
               openings=[{"kind": "door", "wall": "north", "offset": 0.45},
                         {"kind": "window", "wall": "east", "offset": 0.0},
                         {"kind": "window", "wall": "east", "offset": 0.05}],
               dressing=[{"id": "sofa", "prop": item("sofa"), "at": (0, 1.5)},
                         {"id": "table", "prop": item("table"), "at": (0, 1.2)},
                         {"id": "tree", "prop": item("tree"), "at": (-1.5, -1)},
                         {"id": "lamp", "prop": item("lamp"), "at": (2.45, 0)},
                         {"id": "books", "prop": item("books"), "at": (1.5, -1)},
                         {"id": "apple", "prop": item("apple"), "at": (0, 0), "on": "sofa"}])
    assert errors(validate_environment(spec)) == {
        ("lighting", "unknown_lighting"), ("materials.floor_finish", "not_for_template"),
        ("openings[0]", "off_wall"), ("openings[2]", "overlap"),
        ("dressing[1].at", "overlap"), ("dressing[2].prop", "wrong_setting"), ("dressing[2].at", "outside"), ("dressing[3].at", "outside"),
        ("dressing[4].on", "needs_surface"), ("dressing[5].on", "no_surface")}


def test_door_marks_and_cameras_are_checked_against_the_resolved_set():
    spec = env(dressing=[{"id": "shelf", "prop": item("bookshelf"), "at": (-1.92, 2.3)},
                         {"id": "sofa", "prop": item("sofa"), "at": (0.5, 1.9)},
                         {"id": "table", "prop": item("table"), "at": (0.5, 0.6)}],
               marks=[{"name": "by_sofa", "at": (0.5, 1.9)}, {"name": "rug", "at": (-1.0, -1.0)},
                      {"name": "sit", "kind": "seat", "item": "table"}],
               cameras=[{"name": "outside", "at": (0, 5, 1.5), "look_at": (0, 0, 1)}])
    issues = validate_environment(spec)
    assert errors(issues) == {("openings[0]", "door_blocked"), ("marks[0].at", "blocked"), ("marks[2].item", "no_seat")}
    assert ("cameras[0].at", "outside") in {(i.path, i.code) for i in issues if i.severity == "warning"}


def test_things_on_tops_stand_at_the_top():
    spec = env(dressing=[{"id": "table", "prop": item("table"), "at": (0, 0.5)},
                         {"id": "books", "prop": item("books"), "at": (0.2, 0.5), "on": "table"},
                         {"id": "chair", "prop": item("chair"), "at": (0, -0.3), "rotation_deg": 180}],
               marks=[{"name": "chair", "kind": "seat", "item": "chair"}])
    assert errors(validate_environment(spec)) == set()
    r = resolve_environment(spec)
    books = next(d for d in r.dressing if d.id == "books")
    assert books.z == 0.72 and r.mark("chair").at == (0.0, -0.3, 0.44)
    layout = r.layout()
    assert [s[0] for s in layout["solids"]] == ["table", "chair"] and layout["tops"][0][-1] == 0.72
    off = env(dressing=[{"id": "table", "prop": item("table"), "at": (0, 0.5)},
                        {"id": "books", "prop": item("books"), "at": (1.5, 0.5), "on": "table"}])
    assert errors(validate_environment(off)) == {("dressing[1].at", "off_surface")}


def test_outdoor_sets_have_no_walls_and_lighting_presets_exist():
    assert errors(validate_environment(env("park", openings=[{"kind": "door", "wall": "north"}]))) == {
        ("openings", "not_for_template")}
    assert errors(validate_environment(env("park", size=(100, 6, 0)))) == {("size[0]", "out_of_range")}
    assert {"day", "evening", "night", "overcast", "indoor_warm"} <= set(LIGHTING)
    with pytest.raises(ValidationError):
        GenerateEnvironmentParams(spec=env(lighting="disco").model_dump(mode="json"))


# ------------------------------------------------------------------ scenes in a set
def manifest(**kw):
    base = {"name": "Kitchen talk", "environment": {"code": "ENV-001", "version": "1.0"},
            "instances": [{"key": "ali", "character": {"code": "CHAR-001", "version": "1.0"}, "mark": "centre"},
                          {"key": "dev", "character": {"code": "CHAR-002", "version": "1.0"},
                           "transform": {"location": (1.0, -0.5, 0)}}]}
    return SceneManifest.model_validate({**base, **kw})


LAYOUT = resolve_environment(env()).layout()
FACTS = {"PROP-001@1.0": PropFacts(size=(0.24, 0.24, 0.24), solid=False, rests_on="floor"),
         "PROP-002@1.0": PropFacts(size=(1.2, 0.75, 0.72), solid=True, rests_on="floor", top_z=0.72),
         "PROP-003@1.0": PropFacts(size=(0.08, 0.08, 0.09), solid=False, rests_on="top")}


def test_manifest_checks_the_set_fields_without_the_registry():
    m = manifest(environment={"code": "CHAR-009", "version": "1.0"}, lighting="disco",
                 props=[{"key": "ali", "prop": {"code": "ACC-001", "version": "1.0"}}])
    assert {("environment.code", "wrong_kind"), ("lighting", "unknown_lighting"), ("props[0].prop.code", "wrong_kind"),
            ("props[0].key", "duplicate")} <= errors(validate_manifest(m))
    bare = SceneManifest.model_validate({"name": "x", "camera": {"mark": "wide"}, "instances": [
        {"key": "ali", "character": {"code": "CHAR-001", "version": "1.0"}, "mark": "centre"}]})
    assert errors(validate_manifest(bare)) == {("instances[0].mark", "no_environment"), ("camera.mark", "no_environment")}
    assert errors(validate_manifest(manifest())) == set()


def test_layout_places_marks_props_and_characters():
    ok = manifest(camera={"mark": "front"}, props=[
        {"key": "ball", "prop": {"code": "PROP-001", "version": "1.0"}, "at": (-1, -1)},
        {"key": "desk", "prop": {"code": "PROP-002", "version": "1.0"}, "at": (1.5, -1.6)},
        {"key": "apple", "prop": {"code": "PROP-003", "version": "1.0"}, "at": (1.5, -1.6), "on": "desk"},
        {"key": "apple2", "prop": {"code": "PROP-003", "version": "1.0"}, "at": (-0.3, 0.4), "on": "table_1"}])
    assert check_layout(ok, LAYOUT, FACTS) == []
    bad = manifest(camera={"mark": "drone"}, instances=[
        {"key": "ali", "character": {"code": "CHAR-001", "version": "1.0"}, "mark": "kitchen"},
        {"key": "dev", "character": {"code": "CHAR-002", "version": "1.0"}, "transform": {"location": (-0.3, 1.8, 0)}},
        {"key": "sam", "character": {"code": "CHAR-003", "version": "1.0"}, "transform": {"location": (4, 0, 0)}}],
        props=[{"key": "desk", "prop": {"code": "PROP-002", "version": "1.0"}, "at": (-0.3, 0.4)},
               {"key": "apple", "prop": {"code": "PROP-003", "version": "1.0"}, "at": (-2, -1.5)},
               {"key": "ball", "prop": {"code": "PROP-001", "version": "1.0"}, "at": (9, 0)},
               {"key": "unknown", "prop": {"code": "PROP-099", "version": "1.0"}}])
    assert errors(check_layout(bad, LAYOUT, FACTS)) == {
        ("camera.mark", "unknown_mark"), ("instances[0].mark", "unknown_mark"),
        ("instances[1].transform.location", "blocked"), ("instances[2].transform.location", "outside"),
        ("props[0].at", "overlap"), ("props[1].on", "needs_surface"), ("props[2].at", "outside")}


def test_start_transform_uses_the_mark_and_moves_from_it():
    spec = env(marks=[{"name": "window", "at": (2.0, 0.0), "facing_deg": 90}])
    layout = resolve_environment(spec).layout()
    m = manifest(instances=[{"key": "ali", "character": {"code": "CHAR-001", "version": "1.0"}, "mark": "window",
                             "transform": {"location": (0, 0.5, 0), "rotation_deg": (0, 0, 10)}}])
    t = start_transform(m.instances[0], layout)
    assert t.location == (2.0, 0.5, 0.0) and t.rotation_deg == (0, 0, 100)
    assert start_transform(m.instances[0], None) == m.instances[0].transform
