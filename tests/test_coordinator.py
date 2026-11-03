import json
from pathlib import Path

import pytest

from studio.agents.stubs import FIXTURE_DIR, stub_registry
from studio.coordinator import (
    AgentRegistry,
    AwaitingApproval,
    Coordinator,
    PipelineError,
    RunStore,
    StubAgent,
)
from studio.coordinator.gates import GateDecision, ScriptedGate
from studio.coordinator.pipeline import decide, load_manifest
from studio.schemas import Config, ContinuityReport, ProductionPackage


def _config(**overrides) -> Config:
    data = {
        "story_text": (FIXTURE_DIR / "story.txt").read_text(),
        "target_duration_sec": 30,
        "human_approval": {"mode": "none"},
    }
    data.update(overrides)
    return Config.model_validate(data)


@pytest.fixture
def manifest():
    return load_manifest(FIXTURE_DIR / "manifest.json")


def test_full_run_with_stubs(tmp_path, manifest):
    store = RunStore(tmp_path / "run")
    coord = Coordinator(_config(), manifest, stub_registry(), store, project_id="lantern")
    path = coord.run()
    pkg = ProductionPackage.model_validate_json(Path(path).read_text())
    assert pkg.status == "ready"
    assert pkg.stats.shots == 3 and pkg.stats.scenes == 2
    assert pkg.scenes[0].frame_start == 1 and pkg.scenes[0].frame_end == 360
    assert pkg.scenes[1].frame_start == 361
    assert (tmp_path / "run" / "exports" / "screenplay.md").read_text().startswith("# The Lantern Fox")
    assert "sh_01_01" in (tmp_path / "run" / "exports" / "shot_list.csv").read_text()
    assert store.load_state()["status"] == "done"
    assert len(store.load_approvals().records) == 6  # one auto record per agent


def test_retry_then_fail_on_bad_output(tmp_path, manifest):
    registry = stub_registry()
    bad = {"title": "x"}  # fails schema
    registry.register(StubAgent("story_analyst", bad, bad, bad, bad))
    store = RunStore(tmp_path / "run")
    coord = Coordinator(_config(), manifest, registry, store)
    with pytest.raises(PipelineError) as exc:
        coord.run()
    assert exc.value.agent == "story_analyst"
    assert exc.value.issues and exc.value.issues[0].category == "schema"
    assert registry.get("story_analyst").calls[-1].attempt == 3  # 1 + 2 retries
    assert store.load_state()["status"] == "failed"


def test_retry_recovers_with_feedback(tmp_path, manifest):
    registry = stub_registry()
    good = json.loads((FIXTURE_DIR / "characters.json").read_text())
    bad = json.loads(json.dumps(good))
    bad["characters"][0]["asset"]["library_id"] = "lib_char_nobody"  # fails hard check 3
    agent = StubAgent("character_designer", bad, good)
    registry.register(agent)
    coord = Coordinator(_config(), manifest, registry, RunStore(tmp_path / "run"))
    coord.run()
    assert len(agent.calls) == 2
    assert agent.calls[1].feedback and agent.calls[1].feedback[0].category == "missing_asset"


def test_file_gate_pauses_and_resumes(tmp_path, manifest):
    run_dir = tmp_path / "run"
    config = _config(human_approval={"mode": "selected", "agents": ["story_analyst"]})
    coord = Coordinator(config, manifest, stub_registry(), RunStore(run_dir))
    with pytest.raises(AwaitingApproval) as exc:
        coord.run()
    assert exc.value.agent == "story_analyst"
    assert (run_dir / "review" / "story_analyst.json").exists()
    assert RunStore(run_dir).load_state()["status"] == "awaiting_approval"

    decide(run_dir, "story_analyst", "approve", notes="looks right")
    coord = Coordinator(config, manifest, stub_registry(), RunStore(run_dir))
    path = coord.run()
    assert path.exists()
    records = RunStore(run_dir).load_approvals().records
    assert records[0].agent == "story_analyst" and records[0].decision == "approve"
    assert records[0].notes == "looks right"


def test_gate_revise_reruns_agent_with_notes(tmp_path, manifest):
    registry = stub_registry()
    agent = registry.get("director")
    gate = ScriptedGate(answers={"director": [GateDecision(decision="revise", notes="tighter opening")]})
    coord = Coordinator(_config(human_approval={"mode": "all"}), manifest, registry, RunStore(tmp_path / "run"), gate=gate)
    coord.run()
    assert len(agent.calls) == 2
    assert agent.calls[1].revise_notes == "tighter opening"
    assert ("director", 2) in gate.seen


def test_gate_edit_replaces_output(tmp_path, manifest):
    registry = stub_registry()
    edited = json.loads((FIXTURE_DIR / "story_bible.json").read_text())
    edited["title"] = "The Lantern Fox (edited)"
    gate = ScriptedGate(answers={"story_analyst": [GateDecision(decision="edit", edited_payload=edited)]})
    store = RunStore(tmp_path / "run")
    coord = Coordinator(_config(human_approval={"mode": "all"}), manifest, registry, store, gate=gate)
    coord.run()
    assert store.load_output("story_analyst").title == "The Lantern Fox (edited)"


def test_continuity_fix_loop_routes_to_owner(tmp_path, manifest):
    registry = stub_registry()
    director = registry.get("director")
    fail = ContinuityReport.model_validate(
        {
            "status": "fail",
            "issues": [
                {
                    "id": "iss_001",
                    "severity": "error",
                    "category": "character_logic",
                    "where": "sh_02_01",
                    "message": "Mia should still hold the lantern",
                    "owner_agent": "director",
                }
            ],
        }
    )
    checker = StubAgent("continuity_checker", fail, {"status": "pass", "issues": []})
    registry.register(checker)
    store = RunStore(tmp_path / "run")
    coord = Coordinator(_config(), manifest, registry, store)
    path = coord.run()
    assert len(director.calls) == 2
    assert director.calls[1].feedback[0].message.startswith("Mia should")
    assert store.load_state()["fix_round"] == 1
    pkg = ProductionPackage.model_validate_json(path.read_text())
    assert pkg.status == "ready"
    assert len(list((store.root / "outputs").glob("director.v*.json"))) == 2


def test_fix_loop_gives_up_and_marks_needs_review(tmp_path, manifest):
    registry = stub_registry()
    fail = {
        "status": "fail",
        "issues": [{"id": "iss_001", "severity": "error", "category": "timeline", "message": "always wrong", "owner_agent": "layout_td"}],
    }
    registry.register(StubAgent("continuity_checker", fail))
    config = _config(flags={"continuity_fix_rounds": 1})
    store = RunStore(tmp_path / "run")
    path = Coordinator(config, manifest, registry, store).run()
    pkg = ProductionPackage.model_validate_json(path.read_text())
    assert pkg.status == "needs_review"
    assert store.load_state()["fix_round"] == 1


def test_open_build_request_blocks_package(tmp_path, manifest):
    registry = stub_registry()
    chars = json.loads((FIXTURE_DIR / "characters.json").read_text())
    chars["characters"][1]["asset"] = {
        "status": "build_request", "rig_type": "rigify_quadruped", "height_m": 0.5, "build_request_id": "br_char_fox",
    }
    chars["build_requests"] = [{"id": "br_char_fox", "kind": "character", "for_id": "char_fox", "spec": "A glowing fox", "rig_type": "rigify_quadruped"}]
    registry.register(StubAgent("character_designer", chars))
    path = Coordinator(_config(), manifest, registry, RunStore(tmp_path / "run")).run()
    pkg = ProductionPackage.model_validate_json(path.read_text())
    assert pkg.status == "blocked_on_assets"
    assert pkg.stats.open_build_requests == 1


def test_render_backend_none_skips_layout(tmp_path, manifest):
    config = _config(flags={"render_backend": "none", "layout_td": False})
    assert config.agents_enabled() == ["story_analyst", "character_designer", "scene_builder", "director", "continuity_checker"]
    path = Coordinator(config, manifest, stub_registry(), RunStore(tmp_path / "run")).run()
    pkg = ProductionPackage.model_validate_json(path.read_text())
    assert pkg.scenes[0].shots[0].layout is None
    assert pkg.stats.total_frames == 720


def test_cli_run_and_approve(tmp_path):
    from studio.cli import main

    run_dir = tmp_path / "cli"
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "target_duration_sec": 30,
        "library_manifest_path": str(FIXTURE_DIR / "manifest.json"),
        "human_approval": {"mode": "selected", "agents": ["story_analyst"]},
    }))
    args = ["run", "--story", str(FIXTURE_DIR / "story.txt"), "--run-dir", str(run_dir), "--config", str(cfg), "--agents", "stub"]
    assert main(args) == 2  # paused at the gate
    assert main(["approve", str(run_dir), "story_analyst"]) == 0
    assert main(args) == 0
    assert (run_dir / "production_package.json").exists()
    assert main(["status", str(run_dir)]) == 0
