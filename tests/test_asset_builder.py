"""Asset build phase 4: the sandbox static check always; the QA loop end to end when bpy is
importable (scripts run in a child process, the turntable renders with Cycles on the CPU)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from studio.agents.stubs import FIXTURE_DIR
from studio.assets.builder import BOX_SCRIPT, GeneratorLibrary, StubAssetBuilder, StubRenderQA, build_props, library_id_for, promote_props
from studio.schemas import Config, Manifest, World
from studio.schemas.build_requests import BuildRequest
from tools.asset_builder.sandbox import run_script, static_check


def _request(**over) -> BuildRequest:
    base = {"id": "br_prop_lantern_b", "kind": "prop", "for_id": "prop_lantern_b", "spec": "A dented tin lantern with a wire handle, taller than it is wide", "bounds_m": (0.2, 0.2, 0.4), "nearest_library_id": "lib_prop_lantern"}
    return BuildRequest.model_validate({**base, **over})


def _world_with_request() -> World:
    data = json.loads((FIXTURE_DIR / "world.json").read_text())
    data["props"].append({"id": "prop_lantern_b", "name": "Tin lantern", "owner_id": "char_mia", "asset": {"status": "build_request", "build_request_id": "br_prop_lantern_b"}})
    data["build_requests"].append(_request().model_dump(mode="json"))
    return World.model_validate(data)


def test_static_check_accepts_the_box_and_rejects_escapes():
    assert static_check(BOX_SCRIPT) == []
    bad = "import os\nimport bpy\n\ndef build(params):\n    open('x', 'w').write('hi')\n    bpy.ops.wm.save_as_mainfile(filepath='x.blend')\n    getattr(bpy, 'ops')\n    bpy.data.images[0].__class__\n"
    errors = static_check(bad)
    assert any("'os'" in e for e in errors) and any("'open'" in e for e in errors) and any("'getattr'" in e for e in errors)
    assert any("save_as_mainfile" in e or "'wm'" in e for e in errors) and any("dunder" in e for e in errors)
    assert static_check("import bpy\n") == ["the script must define a top-level function build(params)"]
    assert static_check("def build(a, b):\n    pass\n") == ["build must take exactly one argument, params"]
    assert static_check("def build(params):\n    from subprocess import run\n") == ["line 2: import from 'subprocess' is not allowed"]
    assert static_check("def build(params:\n") [0].startswith("syntax error")


def test_library_id_and_generator_matching(tmp_path):
    assert library_id_for(_request()) == "lib_prop_lantern_b"
    assert library_id_for(_request(for_id="prop_Old-Chair")) == "lib_prop_old_chair"
    gens = GeneratorLibrary(tmp_path)
    assert gens.match(["lantern", "light"]) is None
    (tmp_path / "generators" / "scripts").mkdir(parents=True)
    (tmp_path / "generators" / "scripts" / "a.py").write_text(BOX_SCRIPT)
    entry = gens.add(_request(), "generators/scripts/a.py", {"height": 0.4}, ["lantern", "light", "metal"], "lib_prop_lantern_b", 0.9)
    assert entry["id"] == "gen_script_lantern_b" and (tmp_path / "generators" / "scripts" / "index.json").exists()
    again = GeneratorLibrary(tmp_path)
    assert again.match(["lantern", "light"])["library_id"] == "lib_prop_lantern_b"
    assert again.match(["chair"]) is None
    assert again.script_text(entry) == BOX_SCRIPT


def test_promote_props_points_accepted_props_at_the_library():
    world = _world_with_request()
    accepted = world.build_requests[0].model_copy(update={"status": "accepted", "result_library_id": "lib_prop_lantern_b"})
    promoted = promote_props(world, {accepted.id: accepted})
    prop = next(p for p in promoted.props if p.id == "prop_lantern_b")
    assert prop.asset.status == "library" and prop.asset.library_id == "lib_prop_lantern_b"
    assert promoted.build_requests[0].status == "accepted"
    untouched = promote_props(world, {})
    assert untouched.props[-1].asset.status == "build_request"


def test_sandbox_runs_and_refuses(tmp_path):
    pytest.importorskip("bpy")
    lib = tmp_path / "library"
    (lib / "props").mkdir(parents=True)
    out = lib / "props" / "lib_prop_box.blend"
    res = run_script(lib, BOX_SCRIPT, {"width": 0.2, "depth": 0.2, "height": 0.4}, out, {"studio_kind": "prop", "studio_tags": "box"}, time_limit_s=120)
    assert res.ok, res.error
    assert out.exists() and res.objects == 2 and res.polys > 6
    assert res.bounds_m[2] == pytest.approx(0.4, abs=0.02) and res.bounds_m[0] == pytest.approx(0.2, abs=0.02) and res.min_z == pytest.approx(0.0, abs=1e-3)
    # wrong output location, failing script, hanging script
    assert not run_script(lib, BOX_SCRIPT, {}, lib / "envs" / "x.blend", {}).ok
    crash = "import bpy\n\ndef build(params):\n    raise ValueError('boom')\n"
    res = run_script(lib, crash, {}, out, {}, time_limit_s=120)
    assert not res.ok and "boom" in res.error
    hang = "import bpy\n\ndef build(params):\n    while True:\n        pass\n"
    res = run_script(lib, hang, {}, out, {}, time_limit_s=3)
    assert not res.ok and "time limit" in res.error
    empty = "import bpy\n\ndef build(params):\n    bpy.data.objects.new('e', None)\n"
    assert "no mesh" in run_script(lib, empty, {}, out, {}, time_limit_s=120).error


def test_qa_loop_accepts_then_parks(tmp_path):
    pytest.importorskip("bpy")
    from tools import lint_library, scan_library

    lib = tmp_path / "library"
    shutil.copytree("library", lib)
    world = _world_with_request()
    manifest = Manifest.model_validate_json((lib / "manifest.json").read_text())
    config = Config(story_text="x", target_duration_sec=1, flags={"asset_builder_rounds": 2})

    builder, qa = StubAssetBuilder(), StubRenderQA()
    records, promoted = build_props(lib, world, manifest, config, builder, qa, time_limit_s=120)
    assert len(records) == 1 and records[0]["status"] == "accepted" and builder.calls == 1
    br = promoted.build_requests[0]
    assert br.status == "accepted" and br.result_library_id == "lib_prop_lantern_b" and br.decision.by == "qa_agent"
    assert len(br.attempts) == 1 and br.attempts[0].qa_score == 0.9 and br.attempts[0].turntable == "preview/lib_prop_lantern_b.png"
    assert (lib / br.attempts[0].script).read_text().strip() == BOX_SCRIPT.strip()
    assert (lib / "props" / "lib_prop_lantern_b.blend").exists() and (lib / "preview" / "lib_prop_lantern_b.png").stat().st_size > 1000
    assert next(p for p in promoted.props if p.id == "prop_lantern_b").asset.library_id == "lib_prop_lantern_b"
    assert GeneratorLibrary(lib).match(["dented", "lantern", "wire", "prop"]) is not None

    rescanned = Manifest.model_validate(scan_library.scan(lib))
    prop = next(p for p in rescanned.props if p.id == "lib_prop_lantern_b")
    assert prop.attach_bone == "hand.R" and prop.bounds_m[2] == pytest.approx(0.4, abs=0.02) and "lantern" in prop.tags
    assert lint_library.main(["--library", str(lib)]) == 0

    # A second request the QA never accepts: two rounds, then parked for a human.
    world2 = _world_with_request()
    world2.build_requests[0] = _request(id="br_prop_crate", for_id="prop_crate", spec="A crate", bounds_m=(1, 1, 1))
    world2.props[-1].asset.build_request_id = "br_prop_crate"
    world2 = World.model_validate(world2.model_dump(mode="json"))
    strict = StubRenderQA(score=0.4)
    records, promoted = build_props(lib, world2, manifest, config, StubAssetBuilder(), strict, time_limit_s=120, render=False)
    br = promoted.build_requests[0]
    assert records[0]["status"] == "review" and br.status == "review" and len(br.attempts) == 2 and br.decision is None
    assert not (lib / "props" / "lib_prop_crate.blend").exists() and (lib / "preview" / "pending" / "lib_prop_crate.blend").exists()
    assert promoted.props[-1].asset.status == "build_request"
    # A script that fails the static check is recorded as an attempt, not run.
    bad_builder = StubAssetBuilder(script="import os\n\ndef build(params):\n    pass\n")
    records, _ = build_props(lib, world2, manifest, Config(story_text="x", target_duration_sec=1, flags={"asset_builder_rounds": 1}), bad_builder, strict, render=False)
    assert records[0]["rounds"][0]["outcome"] == "static_check" and "'os'" in records[0]["rounds"][0]["notes"]


def test_agents_build_their_messages(tmp_path):
    from studio.agents.asset_builder import AssetBuilder, BuildScript
    from studio.agents.render_qa import QAVerdict, RenderQA
    from studio.schemas.build_requests import BuildAttempt
    from tests.fakes import FakeClient, FakeResponse

    manifest = Manifest.model_validate_json((FIXTURE_DIR / "manifest.json").read_text())
    config = Config(story_text="x", target_duration_sec=1)
    client = FakeClient(FakeResponse(BuildScript(script=BOX_SCRIPT, params={"height": 0.4}, tags=["lantern"])))
    prev = [BuildAttempt(route="asset_builder", round=1, script="generators/scripts/br_prop_lantern_b_r1.py", qa_score=0.5, qa_notes="handle too thick")]
    out = AssetBuilder(client=client).write_script(_request(), manifest, config, previous=prev, scripts={prev[0].script: "# old script"})
    assert out.script.strip() == BOX_SCRIPT.strip()
    text = client.messages.calls[0]["messages"][0]["content"]
    assert "handle too thick" in text and "# old script" in text and "lib_prop_lantern" in text and "dented tin lantern" in text
    assert client.messages.calls[0]["output_format"] is BuildScript

    sheet = tmp_path / "s.png"
    sheet.write_bytes(b"\x89PNG\r\n\x1a\n")
    client = FakeClient(FakeResponse(QAVerdict(score=0.85, problems=["handle too thin"], notes="ok")))
    verdict = RenderQA(client=client).review(sheet, _request(), {"bounds_m": [0.2, 0.2, 0.4], "polys": 100}, config)
    assert verdict.score == 0.85 and verdict.summary() == "handle too thin"
    content = client.messages.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "image" and "polys" in content[1]["text"]
    refused = FakeClient(FakeResponse(None, stop_reason="refusal"))
    assert RenderQA(client=refused).review(sheet, _request(), {}, config).score == 0.0
