"""The Story Analyst against a fake Claude client. The real call is one
``client.messages.parse`` with structured output; the fake records the request and
returns a canned bible so the prompt assembly, parsing and coordinator wiring are tested
without network access."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio.agents import claude_registry
from studio.agents.base import AgentRefused
from studio.agents.story_analyst import StoryAnalyst
from studio.agents.stubs import FIXTURE_DIR, stub_registry
from studio.coordinator import AgentContext, Coordinator, PipelineError, RunStore
from studio.coordinator.pipeline import load_manifest
from studio.schemas import Config, StoryBible
from studio.schemas.continuity import Issue
from tests.fakes import FakeClient, FakeMessages, FakeResponse


@pytest.fixture
def bible_payload():
    return json.loads((FIXTURE_DIR / "story_bible.json").read_text())


@pytest.fixture
def config():
    return Config(story_text=(FIXTURE_DIR / "story.txt").read_text(), target_duration_sec=30, human_approval={"mode": "none"})


def _ctx(config, **kw):
    return AgentContext(config=config, manifest=load_manifest(FIXTURE_DIR / "manifest.json"), **kw)


def test_prompt_contains_story_config_and_rules(config, bible_payload):
    client = FakeClient(FakeResponse(StoryBible.model_validate(bible_payload)))
    agent = StoryAnalyst(client=client)
    result = agent.run(_ctx(config))
    assert isinstance(result, StoryBible) and result.title == "The Lantern Fox"

    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_format"] is StoryBible
    assert call["output_config"] == {"effort": "high"}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "char_<slug>" in call["system"][0]["text"]
    user = call["messages"][0]["content"]
    assert "glowing fox" in user and "target_duration_sec" in user and "## Task" in user
    assert "Problems with your previous attempt" not in user
    assert agent.last_usage["input_tokens"] == 1000


def test_feedback_and_revision_notes_are_in_the_prompt(config, bible_payload):
    client = FakeClient(FakeResponse(StoryBible.model_validate(bible_payload)))
    issue = Issue(id="iss_001", severity="error", category="schema", message="beats.0.location_id: unknown", owner_agent="story_analyst", suggested_fix="Use loc_forest_clearing")
    StoryAnalyst(client=client).run(_ctx(config, attempt=2, feedback=[issue], revise_notes="Make Mia older"))
    user = client.messages.calls[0]["messages"][0]["content"]
    assert "Problems with your previous attempt (attempt 1)" in user
    assert "beats.0.location_id: unknown" in user and "Use loc_forest_clearing" in user
    assert "Revision notes from the human reviewer" in user and "Make Mia older" in user


def test_refusal_raises(config):
    client = FakeClient(FakeResponse(None, stop_reason="refusal"))
    with pytest.raises(AgentRefused):
        StoryAnalyst(client=client).run(_ctx(config))


def test_truncation_raises(config):
    client = FakeClient(FakeResponse(None, stop_reason="max_tokens"))
    with pytest.raises(RuntimeError, match="truncated"):
        StoryAnalyst(client=client).run(_ctx(config))


def test_story_analyst_inside_the_coordinator(tmp_path, config, bible_payload):
    """Real agent for story_analyst, stubs for the rest, full pipeline to a package."""
    client = FakeClient(FakeResponse(StoryBible.model_validate(bible_payload)))
    registry = stub_registry()
    registry.register(claude_registry(client=client).get("story_analyst"))
    store = RunStore(tmp_path / "run")
    path = Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, store).run()
    assert Path(path).exists()
    assert store.load_output("story_analyst").title == "The Lantern Fox"
    assert len(client.messages.calls) == 1


def test_coordinator_retries_a_failed_agent_call(tmp_path, config, bible_payload):
    """An exception from the API counts as a failed attempt and is retried with feedback."""
    good = StoryBible.model_validate(bible_payload)

    class FlakyMessages(FakeMessages):
        def parse(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return FakeResponse(None, stop_reason="refusal")
            return FakeResponse(good)

    client = FakeClient()
    client.messages = FlakyMessages([])
    registry = stub_registry()
    registry.register(StoryAnalyst(client=client))
    store = RunStore(tmp_path / "run")
    Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, store).run()
    assert len(client.messages.calls) == 2
    assert "declined" in client.messages.calls[1]["messages"][0]["content"]


def test_agent_exception_exhausts_retries(tmp_path, config):
    client = FakeClient(FakeResponse(None, stop_reason="refusal"))
    registry = stub_registry()
    registry.register(StoryAnalyst(client=client))
    with pytest.raises(PipelineError):
        Coordinator(config, load_manifest(FIXTURE_DIR / "manifest.json"), registry, RunStore(tmp_path / "run")).run()
    assert len(client.messages.calls) == 3
