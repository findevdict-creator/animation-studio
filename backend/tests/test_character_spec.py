import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from studio_contracts.character import (CharacterSpecification, InvalidSpecification, llm_json_schema, resolve_spec,
                                        validate_spec)
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1
from studio_contracts.style_profile import FAMILY_CARTOON_V1, get_style_profile

ALI = {
    "name": "Ali",
    "body": {"age_appearance": "child", "body_type": "average", "height_m": 1.28, "head_body_ratio": 0.34,
             "proportions": {"leg_length": 0.1, "belly": -0.2}},
    "face": {"shape": "rounded", "eye_shape": "large_rounded", "eye_size": 0.7, "eye_color": "#5a3a1e",
             "nose": "small", "mouth": "friendly", "sliders": {"cheek_fullness": 0.4}},
    "hair": {"style": "short", "color": "black"},
    "materials": {"skin": {"preset": "warm_medium"}},
}


def test_valid_spec_resolves_to_numbers():
    spec = CharacterSpecification.model_validate(ALI)
    assert validate_spec(spec) == []
    r = resolve_spec(spec)
    assert (r.height_m, r.head_body_ratio, r.fit_profile) == (1.28, 0.34, "child_average")
    assert r.eye_color == "#5A3A1E" and r.hair_color == FAMILY_CARTOON_V1.hair_colors["black"]
    assert r.proportions["leg_length"] == 0.1 and r.proportions["arm_length"] == 0.0
    assert r.template == "TPL-HUMANOID-STANDIN@0.1" and r.rig_profile == "cartoon_humanoid@1.0"
    assert r.digest() == resolve_spec(CharacterSpecification.model_validate(ALI)).digest()


def test_every_problem_is_reported_at_once():
    bad = {**ALI, "face": {**ALI["face"], "nose": "trunk", "eye_color": "purple", "sliders": {"wings": 1}},
           "hair": {"style": "mohawk", "color": "#12345"}, "overrides": {"x": 1.0},
           "body": {**ALI["body"], "body_type": "giant", "proportions": {"belly": 3}}}
    issues = validate_spec(CharacterSpecification.model_validate(bad))
    paths = {i.path for i in issues if i.severity == "error"}
    assert paths == {"face.nose", "face.eye_color", "face.sliders.wings", "hair.style", "hair.color", "overrides",
                     "body.body_type", "body.proportions.belly"}
    with pytest.raises(InvalidSpecification):
        resolve_spec(CharacterSpecification.model_validate(bad))


def test_out_of_range_numbers_clamp_with_a_warning():
    spec = CharacterSpecification.model_validate({**ALI, "body": {"age_appearance": "child", "height_m": 2.5}})
    [w] = validate_spec(spec)
    assert w.severity == "warning" and w.code == "clamped"
    assert resolve_spec(spec).height_m == FAMILY_CARTOON_V1.age_appearances["child"].height_m.max


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        CharacterSpecification.model_validate({**ALI, "superpower": "flight"})


def test_llm_schema_is_closed_and_accepts_a_valid_answer():
    schema = llm_json_schema(FAMILY_CARTOON_V1)
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema)
    answer = {k: ALI[k] for k in ("name", "body", "face", "hair", "materials")}
    answer["face"] = {**answer["face"], "eye_color": "brown"}
    assert list(v.iter_errors(answer)) == []
    assert list(v.iter_errors({**answer, "hair": {"style": "mohawk", "color": "black"}}))
    assert list(v.iter_errors({**answer, "body": {**answer["body"], "proportions": {"wings": 1}}}))
    assert "template" not in schema["properties"] and "overrides" not in schema["properties"]


def test_profiles():
    assert get_style_profile("family_cartoon") is FAMILY_CARTOON_V1
    with pytest.raises(KeyError):
        get_style_profile("anime")
    keys = CARTOON_HUMANOID_V1.shape_keys()
    assert len(keys) == 9 * 3 + 10 + 6 and len(set(keys)) == len(keys)
    assert "expr_happy_mouth" in keys and "vis_MBP" in keys


def test_validate_endpoint(api_as):
    client = api_as("reviewer")
    ok = client.post("/api/v1/characters/spec/validate", json=ALI).json()
    assert ok["valid"] and ok["resolved"]["fit_profile"] == "child_average" and len(ok["digest"]) == 64
    bad = client.post("/api/v1/characters/spec/validate", json={**ALI, "hair": {"style": "mohawk"}}).json()
    assert not bad["valid"] and bad["issues"][0]["path"] == "hair.style"
    shape = client.post("/api/v1/characters/spec/validate", json={"name": ""}).json()
    assert not shape["valid"] and {i["path"] for i in shape["issues"]} >= {"name", "body"}
    assert client.get("/api/v1/style-profiles/family_cartoon@1.0").json()["id"] == "family_cartoon"
    assert client.get("/api/v1/style-profiles/nope").status_code == 404
    assert "properties" in client.get("/api/v1/style-profiles/family_cartoon/llm-schema").json()
    assert client.get("/api/v1/rig-profiles/cartoon_humanoid@1.0").json()["face_control_bone"] == "face_ctrl"


def test_validate_needs_login(anon):
    assert anon.post("/api/v1/characters/spec/validate", json=ALI).status_code == 401
