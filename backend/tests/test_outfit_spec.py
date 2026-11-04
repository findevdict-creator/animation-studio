"""Outfit specification: catalog, conventions, validation, resolution, the validate API
and the interpret_outfit AI task (fake provider)."""

import uuid

import pytest
from app.ai.providers import LLMSettings
from app.ai.tasks.interpret_outfit import interpret, proposal_model, system_prompt
from app.models.ai import AIGeneration
from studio_contracts.character import resolve_spec, validate_spec
from studio_contracts.jobs import InterpretOutfitParams
from studio_contracts.outfit import (
    FAMILY_CARTOON_CLOTHING,
    GARMENTS,
    InvalidOutfit,
    OutfitSpecification,
    fit_profiles,
    get_garment,
    reference_character,
    resolve_outfit,
    validate_outfit,
)
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1
from studio_contracts.style_profile import FAMILY_CARTOON_V1
from studio_testing.llm_fakes import FakeResponse, fake_client_for


def piece(key, **kw):
    g = get_garment(key)
    return {"slot": g.slot, "garment": {"id": g.id, "version": g.version}, **kw}


def spec(**kw):
    base = {"name": "School day", "fit_profiles": ["child_average"],
            "pieces": [piece("tshirt", material={"color": "royal_blue"}), piece("shorts"), piece("sneakers")]}
    return OutfitSpecification.model_validate({**base, **kw})


def codes(issues):
    return {(i.path, i.code) for i in issues if i.severity == "error"}


def test_catalog_is_consistent_with_rig_and_conventions():
    regions = set(CARTOON_HUMANOID_V1.mask_regions)
    for g in GARMENTS:
        assert set(g.covers) <= regions and set(g.hides) <= set(g.covers), g.key
        assert set(g.fabrics) <= set(FAMILY_CARTOON_CLOTHING.fabrics) and g.default_fabric in g.fabrics
        assert g.default_color in FAMILY_CARTOON_CLOTHING.colors
        assert (g.binding == "rigid") == (g.attachment is not None)
        if g.attachment:
            assert g.attachment in CARTOON_HUMANOID_V1.attachment_points
        for name, d in g.params.items():
            assert d.min <= d.default <= d.max, (g.key, name)
    assert get_garment("tshirt") is get_garment("GARM-TSHIRT@0.1")
    with pytest.raises(KeyError):
        get_garment("GARM-TSHIRT@9.9")


def test_fit_profiles_and_reference_body():
    fps = fit_profiles(FAMILY_CARTOON_V1)
    assert {"child_slim", "child_average", "child_stocky", "adult_slim", "adult_average"} <= set(fps)
    ref = reference_character("child_stocky")
    assert validate_spec(ref) == []
    rc = resolve_spec(ref)
    assert rc.fit_profile == "child_stocky" and rc.height_m == FAMILY_CARTOON_V1.age_appearances["child"].height_m.default
    with pytest.raises(KeyError):
        reference_character("toddler_average")


def test_valid_outfit_resolves_with_defaults_and_numbers():
    s = spec(pieces=[piece("tshirt", params={"ease": 0.0}, material={"color": "#ff0000", "trim_color": "white"}),
                     piece("trousers"), piece("boots"), piece("cap"), piece("backpack")])
    assert validate_outfit(s) == []
    r = resolve_outfit(s)
    top, trousers, _boots, cap, bag = r.pieces
    assert top.color == "#FF0000" and top.trim_color == FAMILY_CARTOON_CLOTHING.colors["white"]
    assert top.params == {"ease": 0.0, "sleeve_length": 0.5, "hem": 0.5}
    assert top.clearance == FAMILY_CARTOON_CLOTHING.clearance_m[0] and top.binding == "skinned"
    assert trousers.fabric == "denim" and trousers.trim_color == trousers.color
    assert cap.attach_bone == "DEF-spine.006" and bag.attach_bone == "DEF-spine.003"
    assert r.primary_fit_profile == "child_average" and r.rig_profile == "cartoon_humanoid@1.0"
    assert r.hides == ["foot", "thigh", "torso"] and "head" in r.covers
    assert r.digest() == resolve_outfit(s).digest() and len(r.digest()) == 64
    assert r.digest() != resolve_outfit(spec()).digest()


def test_every_problem_is_reported_at_once():
    s = spec(fit_profiles=["child_average", "giant_average"], pieces=[
        piece("tshirt", params={"ease": 2.0, "zip": 1.0}, material={"fabric": "denim", "color": "plaid"}),
        piece("longsleeve"),
        {"slot": "lower", "garment": {"id": "GARM-BOOTS", "version": "0.1"}},
        {"slot": "headwear", "garment": {"id": "GARM-TOPHAT", "version": "0.1"}},
        piece("shorts", material={"fabric": "chainmail"}),
    ])
    found = codes(validate_outfit(s))
    assert {("fit_profiles[1]", "unknown_fit_profile"), ("fit_profiles", "scope_mismatch"),
            ("pieces[0].params.ease", "out_of_range"), ("pieces[0].params.zip", "unknown_param"),
            ("pieces[0].material.fabric", "not_for_garment"), ("pieces[0].material.color", "unknown_color"),
            ("pieces[1].slot", "duplicate_slot"), ("pieces[2].slot", "wrong_slot"),
            ("pieces[3].garment", "unknown_garment"), ("pieces[4].slot", "duplicate_slot"),
            ("pieces[4].material.fabric", "not_in_vocabulary")} <= found
    with pytest.raises(InvalidOutfit) as exc:
        resolve_outfit(s)
    assert len(exc.value.issues) >= 11


def test_scope_rules():
    assert codes(validate_outfit(spec(scope="character"))) == {("owner_character", "missing")}
    assert validate_outfit(spec(scope="character", owner_character="CHAR-002")) == []
    assert codes(validate_outfit(spec(owner_character="CHAR-002"))) == {("owner_character", "scope_mismatch")}
    assert codes(validate_outfit(spec(scope="multi"))) == {("fit_profiles", "scope_mismatch")}
    assert validate_outfit(spec(scope="multi", fit_profiles=["child_average", "child_slim"])) == []
    assert codes(validate_outfit(spec(scope="multi", fit_profiles=["child_slim", "child_slim"]))) == \
        {("fit_profiles", "duplicate")}


def test_accessories_may_repeat_and_warnings_do_not_block():
    s = spec(pieces=[piece("tshirt"), piece("backpack"), piece("backpack"),
                     piece("cap", material={"trim_color": "red"})], cloth_simulation=True)
    issues = validate_outfit(s)
    assert not codes(issues)
    assert {i.code for i in issues} == {"not_supported"}
    assert resolve_outfit(s).pieces[3].trim_color is None


def test_validate_endpoint_and_catalog(api_as):
    c = api_as("reviewer")
    good = spec().model_dump(mode="json")
    r = c.post("/api/v1/outfits/spec/validate", json=good)
    assert r.status_code == 200 and r.json()["valid"] and r.json()["digest"] == resolve_outfit(spec()).digest()
    bad = c.post("/api/v1/outfits/spec/validate", json={**good, "pieces": []}).json()
    assert not bad["valid"] and bad["issues"][0]["path"] == "pieces"
    bad = c.post("/api/v1/outfits/spec/validate", json={**good, "fit_profiles": ["x"]}).json()
    assert not bad["valid"] and bad["issues"][0]["code"] == "unknown_fit_profile"
    garments = c.get("/api/v1/garments").json()
    assert [g["key"] for g in garments] == [g.key for g in GARMENTS]
    conv = c.get("/api/v1/style-profiles/family_cartoon@1.0/clothing").json()
    assert conv["colors"]["navy"] and "child_average" in conv["fit_profiles"]
    assert c.get("/api/v1/style-profiles/nope@1.0/clothing").status_code == 404


# ------------------------------------------------------------------ AI
PROPOSAL = {"name": "Rainy walk",
            "pieces": [{"garment": "sweater", "fit": "loose", "length": "long", "color": "forest_green",
                        "trim_color": "cream"},
                       {"garment": "trousers", "fit": "regular", "color": "denim_blue"},
                       {"garment": "boots", "fit": "regular", "length": "short", "fabric": "rubber", "color": "yellow"}],
            "unrepresented": ["polka-dot umbrella"], "notes": "Warm and waterproof."}
PARAMS = InterpretOutfitParams(description="A loose green sweater, jeans and yellow wellies with an umbrella.",
                               scope="character", owner_character="CHAR-002", fit_profiles=["child_average"])


def llm_for(*replies):
    model = proposal_model("family_cartoon@1.0")
    parsed = [r if isinstance(r, FakeResponse) else FakeResponse(model.model_validate(r)) for r in replies]
    return LLMSettings(clients={"anthropic": fake_client_for("anthropic", *parsed)})


def test_outfit_proposal_is_closed_and_maps_words_to_numbers():
    model = proposal_model("family_cartoon@1.0")
    with pytest.raises(ValueError):
        model.model_validate({**PROPOSAL, "pieces": [{"garment": "tuxedo", "color": "black"}]})
    with pytest.raises(ValueError):
        model.model_validate({**PROPOSAL, "pieces": [{"garment": "tshirt", "color": "chartreuse"}]})
    assert "scope" not in model.model_json_schema()["properties"]
    assert "sweater: Sweater" in system_prompt(FAMILY_CARTOON_CLOTHING)
    out = interpret(PARAMS, llm_for(PROPOSAL))
    assert out.valid and out.calls == 1
    s = out.spec
    assert s.scope == "character" and s.owner_character == "CHAR-002" and s.fit_profiles == ["child_average"]
    sweater, trousers, boots = s.pieces
    assert sweater.params == {"ease": 0.85, "sleeve_length": 0.96} and sweater.material.trim_color == "cream"
    assert trousers.params == {"ease": 0.4} and boots.params == {"collar": 0.34}
    assert boots.material.fabric == "rubber"


def test_semantic_error_gets_a_repair_turn():
    bad = {**PROPOSAL, "pieces": [*PROPOSAL["pieces"], {"garment": "tshirt", "color": "red"}]}  # two uppers
    out = interpret(PARAMS, llm_for(bad, PROPOSAL))
    assert out.valid and out.calls == 2 and out.repair_turns == 1
    out = interpret(PARAMS, llm_for(bad, bad))
    assert not out.valid and ("pieces[3].slot", "duplicate_slot") in codes(out.issues)


def test_interpret_outfit_job_end_to_end(api_as, db, dispatcher, storage_as):
    from studio_workers.ai_runner import AIRunner

    creator = api_as("creator")
    r = creator.post("/api/v1/jobs", json={"type": "interpret_outfit", "params": PARAMS.model_dump()})
    assert r.status_code == 202 and r.json()["queue"] == "ai", r.text
    job = r.json()
    outcome = AIRunner(llm=llm_for(PROPOSAL), work_storage=storage_as("worker", "work"),
                       dispatcher=dispatcher).run(uuid.UUID(job["id"]))
    assert outcome.status.value == "succeeded"
    data = creator.get(f"/api/v1/jobs/{job['id']}").json()["result"]["data"]
    assert data["valid"] and data["unrepresented"] == ["polka-dot umbrella"]
    check = creator.post("/api/v1/outfits/spec/validate", json=data["spec"]).json()
    assert check["digest"] == data["resolved_digest"]
    with db() as s:
        gen = s.get(AIGeneration, uuid.UUID(data["ai_generation_id"]))
        assert gen.task == "interpret_outfit" and gen.valid
    assert api_as("reviewer").post("/api/v1/jobs", json={"type": "interpret_outfit",
                                                         "params": PARAMS.model_dump()}).status_code == 403
