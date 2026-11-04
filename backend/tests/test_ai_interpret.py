"""interpret_character: closed proposal schema, spec validation, repair, logging, and the
ai worker path. Fake provider clients; real PostgreSQL and S3."""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.ai.providers import LLMSettings
from app.ai.tasks.interpret_character import (AIRefused, interpret, load_images, proposal_model, system_prompt,
                                              to_spec)
from app.models.ai import AIGeneration
from app.models.jobs import Job
from studio_contracts.character import validate_spec
from studio_contracts.jobs import InterpretCharacterParams
from studio_contracts.style_profile import FAMILY_CARTOON_V1
from studio_testing.llm_fakes import FakeOpenAIClient, FakeResponse, fake_client_for

ALI_PROPOSAL = {
    "name": "Ali",
    "body": {"age_appearance": "child", "body_type": "average", "height_m": 1.28,
             "proportions": {"leg_length": 0.1, "belly": -0.2}},
    "face": {"shape": "rounded", "eye_shape": "large_rounded", "eye_size": 0.7, "eye_color": "brown",
             "nose": "small", "mouth": "friendly", "ears": "default", "sliders": {"cheek_fullness": 0.3}},
    "hair": {"style": "short", "color": "black"},
    "skin_preset": "warm_medium",
    "unrepresented": ["round glasses"],
    "notes": "A cheerful eight-year-old.",
}
DESCRIPTION = "Ali is a cheerful eight-year-old boy with short black hair, big brown eyes and round glasses."


def anthropic_llm(*replies):
    model = proposal_model(FAMILY_CARTOON_V1)
    parsed = [r if isinstance(r, FakeResponse) else FakeResponse(model.model_validate(r)) for r in replies]
    return LLMSettings(clients={"anthropic": fake_client_for("anthropic", *parsed)})


def test_proposal_schema_is_closed():
    model = proposal_model(FAMILY_CARTOON_V1)
    assert model is proposal_model(FAMILY_CARTOON_V1)  # cached per profile
    schema = model.model_json_schema()
    hair = schema["$defs"]["ProposedHair"]["properties"]
    assert hair["style"]["enum"] == list(FAMILY_CARTOON_V1.hair_styles)
    assert "template" not in schema["properties"] and "overrides" not in schema["properties"]
    with pytest.raises(ValueError):
        model.model_validate({**ALI_PROPOSAL, "hair": {"style": "mohawk", "color": "black"}})
    assert "family_cartoon@1.0" in system_prompt(FAMILY_CARTOON_V1)


def test_proposal_to_valid_spec():
    llm = anthropic_llm(ALI_PROPOSAL)
    out = interpret(InterpretCharacterParams(description=DESCRIPTION), llm)
    assert out.valid and out.calls == 1 and out.repair_turns == 0
    assert out.spec.name == "Ali" and out.spec.body.proportions == {"leg_length": 0.1, "belly": -0.2}
    assert out.spec.materials.skin.preset == "warm_medium" and out.spec.template is None
    assert validate_spec(out.spec) == []
    call = llm.clients["anthropic"].messages.calls[0]
    assert DESCRIPTION in call["messages"][0]["content"]


def test_given_name_wins():
    out = interpret(InterpretCharacterParams(description=DESCRIPTION, name="Alia"), anthropic_llm(ALI_PROPOSAL))
    assert out.spec.name == "Alia"


def test_semantic_issues_get_one_repair_turn():
    # body_type "stocky" is allowed globally, so force a cross-field error with a custom profile:
    profile = FAMILY_CARTOON_V1.model_copy(update={"age_appearances": {
        **FAMILY_CARTOON_V1.age_appearances,
        "child": FAMILY_CARTOON_V1.age_appearances["child"].model_copy(update={"body_types": ("slim", "average")})}})
    bad = {**ALI_PROPOSAL, "body": {**ALI_PROPOSAL["body"], "body_type": "stocky"}}
    model = proposal_model(FAMILY_CARTOON_V1)
    params = InterpretCharacterParams(description=DESCRIPTION)
    assert any(i.path == "body.body_type" for i in validate_spec(to_spec(model.model_validate(bad), params, profile),
                                                                 profile))
    # through the task, with the real profile, an OpenAI reply that is broken JSON first:
    client = FakeOpenAIClient("{oops", json.dumps(ALI_PROPOSAL))
    llm = LLMSettings(provider="openai", model="gpt-test", clients={"openai": client})
    out = interpret(params, llm)
    assert out.valid and out.calls == 2 and out.repair_turns == 1 and out.provider == "openai"


def test_refusal_raises():
    with pytest.raises(AIRefused):
        interpret(InterpretCharacterParams(description=DESCRIPTION),
                  anthropic_llm(FakeResponse(None, stop_reason="refusal")))


def test_reference_images(storage_as, key_prefix, tmp_path):
    work = storage_as("api", "work")
    png = tmp_path / "ref.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    key = f"uploads/{key_prefix}/ref.png"
    work.put_file(key, png, "image/png")
    parts, refs = load_images(work, [key])
    assert parts[0].media_type == "image/png" and len(refs[0]["sha256"]) == 64
    with pytest.raises(ValueError, match="uploads/"):
        load_images(work, ["jobs/x/out.blend"])
    text_file = tmp_path / "notes.txt"
    text_file.write_text("hello")
    work.put_file(f"uploads/{key_prefix}/notes.png", text_file, "image/png")
    with pytest.raises(ValueError, match="PNG or JPEG"):
        load_images(work, [f"uploads/{key_prefix}/notes.png"])


def test_ai_job_end_to_end(api_as, db, dispatcher, storage_as):
    from studio_workers.ai_runner import AIRunner

    creator = api_as("creator")
    r = creator.post("/api/v1/jobs", json={"type": "interpret_character",
                                           "params": {"description": DESCRIPTION}})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["queue"] == "ai" and dispatcher.sent[-1][1] == "ai"
    outcome = AIRunner(llm=anthropic_llm(ALI_PROPOSAL), work_storage=storage_as("worker", "work"),
                       dispatcher=dispatcher).run(uuid.UUID(job["id"]))
    assert outcome.status.value == "succeeded"
    done = creator.get(f"/api/v1/jobs/{job['id']}").json()
    data = done["result"]["data"]
    assert data["valid"] and data["spec"]["name"] == "Ali" and data["unrepresented"] == ["round glasses"]
    assert creator.post("/api/v1/characters/spec/validate", json=data["spec"]).json()["digest"] == data["resolved_digest"]
    with db() as s:
        gen = s.get(AIGeneration, uuid.UUID(data["ai_generation_id"]))
        assert gen.valid and gen.task == "interpret_character" and gen.provider == "anthropic"
        assert len(gen.prompt_sha256) == 64 and gen.output["name"] == "Ali"
    with pytest.raises(DBAPIError, match="append-only"):
        with db.begin() as s:
            s.execute(text("UPDATE ai_generations SET valid = false WHERE id = :id"), {"id": gen.id})


def test_refused_job_fails_without_retry(api_as, db, dispatcher, storage_as):
    from studio_workers.ai_runner import AIRunner

    job = api_as("creator").post("/api/v1/jobs", json={"type": "interpret_character",
                                                       "params": {"description": "anything"}}).json()
    AIRunner(llm=anthropic_llm(FakeResponse(None, stop_reason="refusal")), work_storage=storage_as("worker", "work"),
             dispatcher=dispatcher).run(uuid.UUID(job["id"]))
    with db() as s:
        row = s.get(Job, uuid.UUID(job["id"]))
        assert row.status == "failed" and row.attempt == 1 and row.error["kind"] == "AIRefused"
        assert s.query(AIGeneration).filter_by(job_id=row.id, valid=False, stop="refusal").count() == 1


def test_transient_error_is_retried(api_as, db, dispatcher, storage_as):
    from studio_workers.ai_runner import AIRunner

    class Boom:
        class messages:
            @staticmethod
            def parse(**_):
                raise ConnectionError("provider unreachable")

    job = api_as("creator").post("/api/v1/jobs", json={"type": "interpret_character",
                                                       "params": {"description": "anything"}}).json()
    outcome = AIRunner(llm=LLMSettings(clients={"anthropic": Boom()}), work_storage=storage_as("worker", "work"),
                       dispatcher=dispatcher).run(uuid.UUID(job["id"]))
    assert outcome.retried
    with db() as s:
        assert s.get(Job, uuid.UUID(job["id"])).status == "queued"
    assert dispatcher.sent[-1][1:] == ("ai", 5)


def test_ai_jobs_need_ai_use(api_as):
    r = api_as("reviewer").post("/api/v1/jobs", json={"type": "interpret_character", "params": {"description": "x y z"}})
    assert r.status_code == 403
