"""Continuity Checker: reads the whole package plus the code report; its errors drive the fix loop."""

from __future__ import annotations

import pytest

from studio.agents import claude_registry
from studio.agents.continuity_checker import ContinuityChecker
from studio.agents.stubs import FIXTURE_DIR, stub_registry
from studio.coordinator import Coordinator, RunStore
from studio.coordinator.pipeline import load_manifest
from studio.schemas import Config, ContinuityReport
from studio.schemas.continuity import Issue
from tests.fakes import FakeClient, FakeResponse


@pytest.fixture
def config():
    return Config(story_text=(FIXTURE_DIR / "story.txt").read_text(), target_duration_sec=30, human_approval={"mode": "none"})


def test_checker_sees_everything_and_the_code_report(tmp_path, config):
    client = FakeClient(FakeResponse(ContinuityReport(status="pass", issues=[])))
    registry = stub_registry()
    registry.register(ContinuityChecker(client=client))
    store = RunStore(tmp_path / "run")
    Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, store).run()
    call = client.messages.calls[0]
    assert call["output_format"] is ContinuityReport
    user = call["messages"][0]["content"]
    for section in ("## Story bible", "## Character sheets", "## World", "## Direction", "## Layout", "## Automated checks already reported"):
        assert section in user
    assert "clip_walk_cautious 1-200" in user and '"holding"' in user
    assert '"design_brief"' not in user
    assert store.load_output("continuity_checker").status == "pass"


def test_checker_error_routes_to_owner_and_is_fixed(tmp_path, config):
    """The checker flags a director problem; the fix loop reruns from the director with
    the issue as feedback; the second check passes."""
    issue = Issue(
        id="iss_001", severity="error", category="character_logic", where="sh_02_01",
        entity_ids=["char_fox"], message="The fox should react to Mia's line.", owner_agent="director",
        suggested_fix="Give the fox an 'alert' expression in sh_02_01.",
    )
    client = FakeClient(FakeResponse(ContinuityReport(status="fail", issues=[issue])), FakeResponse(ContinuityReport(status="pass")))
    registry = stub_registry()
    registry.register(ContinuityChecker(client=client))
    store = RunStore(tmp_path / "run")
    Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, store).run()
    director = registry.get("director")
    assert len(director.calls) == 2 and [i.message for i in director.calls[1].feedback] == ["The fox should react to Mia's line."]
    assert len(client.messages.calls) == 2
    assert store.load_output("continuity_checker").status == "pass"
    assert store.load_state()["fix_round"] == 1


def test_registry_has_all_six_agents():
    names = set(claude_registry(client=FakeClient())._agents)
    assert names == {"story_analyst", "character_designer", "scene_builder", "director", "layout_td", "continuity_checker"}
