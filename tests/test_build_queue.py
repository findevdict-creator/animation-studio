"""Asset build phase 5: ranking, routing, dedupe and metrics over the stage 1 outputs."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from studio.agents.stubs import FIXTURE_DIR
from studio.assets.queue import apply_queue, choose_route, dedupe, metrics, metrics_markdown, plan_queue, queue_table, rank, screen_time, variant_fit
from studio.coordinator.merge import build_package
from studio.schemas import Characters, Config, Direction, Layout, Manifest, StoryBible, World
from studio.schemas.build_requests import BuildRequest


def _load(name, model):
    return model.model_validate_json((FIXTURE_DIR / name).read_text())


def _br(id, kind, for_id, spec, **over):
    return BuildRequest.model_validate({"id": id, "kind": kind, "for_id": for_id, "spec": spec, **over})


class FakeGenerators:
    def __init__(self, hit=None):
        self.hit = hit

    def match(self, tags, kind="prop"):
        return self.hit if self.hit and set(self.hit["tags"]) & set(tags) else None


def test_screen_time_and_rank():
    layout, world = _load("layout.json", Layout), _load("world.json", World)
    frames = screen_time(layout, world)
    total = sum(s.frame_end - s.frame_start + 1 for s in layout.shots)
    assert frames["prop_lantern"] > 0 and 0 < frames["char_mia"] <= total
    assert frames[world.scenes[0].location_id] > 0
    requests = [
        _br("br_prop_a", "prop", "prop_nothing", "A thing nobody films"),
        _br("br_prop_b", "prop", "prop_lantern", "A lantern"),
        _br("br_char_c", "character", "char_mia", "Mia herself"),
    ]
    ranked = rank(requests, frames)
    assert [b.id for b in ranked] == ["br_prop_b", "br_char_c", "br_prop_a"], "the lantern is in every shot"
    assert ranked[0].screen_time_frames == total and ranked[-1].screen_time_frames == 0
    assert screen_time(None) == {}


def test_choose_route_follows_the_order_and_the_flags():
    manifest = _load("manifest.json", Manifest)
    config = Config(story_text="x", target_duration_sec=1)
    near = _br("br_prop_1", "prop", "prop_lantern_b", "A second brass lantern, handheld", nearest_library_id="lib_prop_lantern", bounds_m=(0.2, 0.2, 0.35))
    assert variant_fit(near, manifest)[0] >= 0.6
    assert choose_route(near, manifest, config)[0] == "variant"
    far = _br("br_prop_2", "prop", "prop_crate", "A big wooden crate", nearest_library_id="lib_prop_lantern", bounds_m=(1.2, 1.2, 1.2))
    assert choose_route(far, manifest, config)[0] == "asset_builder"
    hit = {"id": "gen_script_crate", "tags": ["wooden", "crate"]}
    assert choose_route(far, manifest, config, FakeGenerators(hit))[0] == "generator"
    no_builder = Config(story_text="x", target_duration_sec=1, flags={"auto_routes": ["variant"]})
    assert choose_route(far, manifest, no_builder)[0] == "human"
    with_images = _br("br_prop_3", "prop", "prop_vase", "A tall vase", reference_images=["refs/vase.png"])
    img = Config(story_text="x", target_duration_sec=1, flags={"auto_routes": ["variant", "image_to_3d"]})
    assert choose_route(with_images, manifest, img)[0] == "image_to_3d"
    assert choose_route(with_images, manifest, config)[0] == "asset_builder"
    char = _br("br_char_4", "character", "char_owl", "An owl", rig_type="rigify_bird")
    assert choose_route(char, manifest, config) == ("human", "character requests end at a human when no library variant fits")
    char_var = _br("br_char_5", "character", "char_mia_sister", "Mia's sister, same build", nearest_library_id="lib_char_girl_a", rig_type="rigify_human", height_m=1.2)
    assert choose_route(char_var, manifest, config)[0] == "variant"
    clip = _br("br_clip_6", "clip", "clip_x", "A cartwheel", rig_type="rigify_human", frames=48)
    assert choose_route(clip, manifest, config)[0] == "human"


def test_dedupe_and_plan_queue_round_trip():
    chars, world, layout, manifest = _load("characters.json", Characters), _load("world.json", World), _load("layout.json", Layout), _load("manifest.json", Manifest)
    config = Config(story_text="x", target_duration_sec=1)
    requests = [
        _br("br_prop_lamp_a", "prop", "prop_lamp_a", "A rusty oil lamp with a glass chimney"),
        _br("br_prop_lamp_b", "prop", "prop_lamp_b", "A rusty oil lamp with a glass chimney, slightly dented"),
        _br("br_prop_lantern_b", "prop", "prop_lantern", "Brass lantern, handheld, warm light", nearest_library_id="lib_prop_lantern", bounds_m=(0.2, 0.2, 0.35)),
        _br("br_char_owl", "character", "char_owl", "A barn owl", rig_type="rigify_bird", route="human"),
    ]
    assert dedupe(requests) == {"br_prop_lamp_b": "br_prop_lamp_a"}
    planned, records = plan_queue(requests, layout, world, manifest, config)
    by_id = {b.id: b for b in planned}
    assert planned[0].id == "br_prop_lantern_b" and planned[0].route == "variant" and planned[0].screen_time_frames > 0
    assert by_id["br_prop_lamp_a"].route == "asset_builder" and by_id["br_prop_lamp_b"].route == "variant"
    assert by_id["br_char_owl"].route == "human"
    rec = {r["id"]: r for r in records}
    assert rec["br_prop_lamp_b"]["duplicate_of"] == "br_prop_lamp_a" and rec["br_char_owl"]["reason"] == "route already set"
    assert rec["br_prop_lantern_b"]["variant_of"] == "lib_prop_lantern" and "materials" in rec["br_prop_lantern_b"]["variant_changes"]
    table = queue_table(records)
    assert table.splitlines()[1].split()[1] == "br_prop_lantern_b" and "asset_builder" in table

    world2 = World.model_validate({**world.model_dump(mode="json"), "build_requests": [r.model_dump(mode="json") for r in requests[:3]]})
    chars2 = Characters.model_validate({**chars.model_dump(mode="json"), "build_requests": [requests[3].model_dump(mode="json")]})
    c3, w3, l3 = apply_queue(chars2, world2, layout, planned)
    assert [b.route for b in w3.build_requests] == ["asset_builder", "variant", "variant"] and c3.build_requests[0].route == "human"
    assert l3 is not None and len(l3.shots) == len(layout.shots)


def test_metrics_and_package_routing():
    now = datetime.now(timezone.utc)
    reqs = [
        _br("br_prop_a", "prop", "prop_a", "A", status="accepted", route="asset_builder", screen_time_frames=100, decision={"by": "qa_agent", "at": now}, attempts=[{"route": "asset_builder", "round": 1, "qa_score": 0.9}, {"route": "asset_builder", "round": 2, "qa_score": 0.95}]),
        _br("br_prop_b", "prop", "prop_b", "B", status="accepted", route="human", screen_time_frames=50, decision={"by": "human", "at": now}),
        _br("br_char_c", "character", "char_c", "C", status="open", screen_time_frames=50),
        _br("br_prop_d", "prop", "prop_d", "D", status="review", route="asset_builder", attempts=[{"route": "asset_builder", "round": 1, "qa_score": 0.4}]),
    ]
    m = metrics(reqs)
    assert m["opened"] == 4 and m["closed_by_code"] == 1 and m["closed_by_human"] == 1 and m["open"] == 1 and m["in_review"] == 1
    assert m["avg_rounds"] == 1.5 and m["automated_share"] == 0.25 and m["screen_time_share_closed_by_code"] == 0.5
    assert m["by_kind"] == {"character": 1, "prop": 3} and m["by_route"]["unrouted"] == 1
    md = metrics_markdown(m, "film")
    assert md.startswith("# Build requests for film") and "| closed by code | 1 |" in md
    assert metrics([])["automated_share"] == 1.0

    # The package carries ranked, routed requests.
    bible, chars, world, direction, layout, manifest = (_load(n, m) for n, m in (("story_bible.json", StoryBible), ("characters.json", Characters), ("world.json", World), ("direction.json", Direction), ("layout.json", Layout), ("manifest.json", Manifest)))
    config = Config.model_validate({**json.loads((FIXTURE_DIR / "config.json").read_text()), "story_text": (FIXTURE_DIR / "story.txt").read_text()})
    world = World.model_validate({**world.model_dump(mode="json"), "build_requests": [_br("br_prop_x", "prop", "prop_lantern", "Another lantern, taller", nearest_library_id="lib_prop_lantern", bounds_m=(0.2, 0.2, 0.36)).model_dump(mode="json")]})
    package = build_package(project_id="t", config=config, manifest=manifest, bible=bible, characters=chars, world=world, direction=direction, layout=layout, report=None, approvals=[], needs_review=False)
    br = package.build_requests[0]
    assert br.route == "variant" and br.screen_time_frames > 0 and package.stats.open_build_requests == 1 and package.status == "blocked_on_assets"
