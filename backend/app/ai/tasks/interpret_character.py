"""``interpret_character``: a description (plus optional reference images) to a validated
``CharacterSpecification`` (SRS 5.2, architecture 1 sequence diagram).

The AI fills a *proposal* whose every enumerated field is a closed ``Literal`` built from the
style profile, so structured output cannot hold a value the template lacks. The proposal is
turned into a specification by code, checked with ``validate_spec`` (the same check the API
runs), and sent back once for repair if it still has errors. The AI never sets the template,
rig profile, overrides or seed, and it never publishes anything: the result is a suggestion
a person reviews and edits before a build.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model
from sqlalchemy.orm import Session, sessionmaker

from app.ai.providers import ImagePart, LLMRequest, LLMSettings
from app.ai.providers.types import LLMError
from app.models.ai import AIGeneration
from studio_contracts.character import CharacterSpecification, Issue, Ref, resolve_spec, validate_spec
from studio_contracts.jobs import InterpretCharacterParams
from studio_contracts.style_profile import StyleProfile, get_style_profile

log = logging.getLogger("studio.ai")
TASK = "interpret_character"
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class AIRefused(LLMError):
    """The model declined or ran out of tokens; retrying the same request will not help."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _lit(values) -> Any:
    return Literal[tuple(values)]  # type: ignore[valid-type]


def _sliders(name: str, names: tuple[str, ...]) -> type[BaseModel]:
    return create_model(name, __base__=_Strict, **{
        n: (float, Field(default=0.0, ge=-1, le=1)) for n in names})  # type: ignore[call-overload]


def proposal_model(profile: StyleProfile) -> type[BaseModel]:
    """The output model for ``profile``: closed vocabularies, bounded numbers."""
    return _proposal_model(profile.ref)


@lru_cache(maxsize=16)
def _proposal_model(ref: str) -> type[BaseModel]:
    profile = get_style_profile(ref)
    ages = list(profile.age_appearances)
    body = create_model(
        "ProposedBody", __base__=_Strict,
        age_appearance=(_lit(ages), ...),
        body_type=(_lit(profile.body_types), "average"),
        height_m=(float, Field(description="metres; the style profile clamps it per age")),
        head_body_ratio=(float | None, Field(default=None, description="head height / total height; null for the style default")),
        proportions=(_sliders("BodyProportions", profile.body_sliders), Field(default_factory=dict)),
    )
    face = create_model(
        "ProposedFace", __base__=_Strict,
        shape=(_lit(profile.face_shapes), ...), eye_shape=(_lit(profile.eye_shapes), ...),
        eye_size=(float, Field(ge=0, le=1)), eye_color=(_lit(profile.eye_colors), ...),
        nose=(_lit(profile.noses), ...), mouth=(_lit(profile.mouths), ...), ears=(_lit(profile.ears), ...),
        sliders=(_sliders("FaceSliders", profile.face_sliders), Field(default_factory=dict)),
    )
    hair = create_model("ProposedHair", __base__=_Strict, style=(_lit(profile.hair_styles), ...),
                        color=(_lit(profile.hair_colors), ...))
    return create_model(
        "CharacterProposal", __base__=_Strict,
        name=(str, Field(min_length=1, max_length=80)),
        body=(body, ...), face=(face, ...), hair=(hair, ...),
        skin_preset=(_lit(profile.skin_presets), ...),
        unrepresented=(list[str], Field(default_factory=list, max_length=20,
                                        description="details from the description this style cannot show yet")),
        notes=(str, Field(default="", max_length=600, description="one or two sentences on the main choices")),
    )


def system_prompt(profile: StyleProfile) -> str:
    ages = ", ".join(f"{k} ({v.height_m.min}-{v.height_m.max} m)" for k, v in profile.age_appearances.items())
    return f"""You design characters for a 3D cartoon series. Turn the user's description into a character
proposal for the style profile "{profile.ref}": {profile.description}

The proposal drives a procedural Blender template, so you may only choose values the schema allows.
Rules:
- Pick the closest allowed value for every field. Never invent a new value; if the description asks for
  something the template cannot show (glasses, a hat, wings, freckles, a specific outfit), leave it out of the
  fields and list it in `unrepresented`.
- Age appearances and their heights: {ages}. Choose a height inside the range for the age.
- Sliders run from -1 to 1 with 0 as the template default. Use small values (within +/-0.5) unless the
  description stresses a feature; leave a slider out when the description says nothing about it.
- eye_size runs from 0 (small) to 1 (very large); this style's default is {profile.eye_size.default}.
- Keep the character friendly and suitable for a family audience. Do not base it on a real, identifiable person.
- Use the character's name from the description; if there is none, choose a short friendly name.
- Clothing is not part of the character; outfits are separate assets.
A person reviews and edits your proposal before anything is built."""


def user_prompt(params: InterpretCharacterParams, issues: list[Issue] | None = None, previous: str | None = None) -> str:
    text = f"Character description:\n{params.description.strip()}"
    if params.name:
        text += f"\n\nThe character's name is {params.name!r}; keep it."
    if issues:
        text += ("\n\nYour previous proposal was:\n" + (previous or "") +
                 "\n\nIt failed these checks; return a corrected complete proposal:\n" +
                 "\n".join(f"- {i.path}: {i.message}" for i in issues))
    return text


def to_spec(proposal: BaseModel, params: InterpretCharacterParams, profile: StyleProfile) -> CharacterSpecification:
    p = proposal.model_dump()
    body, face = p["body"], p["face"]
    return CharacterSpecification(
        name=params.name or p["name"],
        description=params.description[:2000],
        style_profile=Ref(id=profile.id, version=profile.version),
        body={**body, "proportions": {k: v for k, v in body["proportions"].items() if v}},
        face={**face, "sliders": {k: v for k, v in face["sliders"].items() if v}},
        hair=p["hair"],
        materials={"skin": {"preset": p["skin_preset"]}},
        reference_images=list(params.reference_image_keys),
    )


def _sniff(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    raise ValueError("reference images must be PNG or JPEG")


def load_images(storage: Any, keys: list[str]) -> tuple[list[ImagePart], list[dict[str, str]]]:
    parts, refs = [], []
    for key in keys:
        if not key.startswith("uploads/") or ".." in key:
            raise ValueError(f"reference image key {key!r} is not under uploads/")
        data = storage.get_bytes(key)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"reference image {key!r} is larger than {MAX_IMAGE_BYTES} bytes")
        parts.append(ImagePart(data=data, media_type=_sniff(data)))
        refs.append({"key": key, "sha256": hashlib.sha256(data).hexdigest()})
    return parts, refs


@dataclass
class Interpretation:
    spec: CharacterSpecification | None
    issues: list[Issue]
    provider: str
    model: str
    stop: str = "ok"
    calls: int = 0
    repair_turns: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    proposal: dict[str, Any] | None = None
    prompt_sha256: str = ""

    @property
    def valid(self) -> bool:
        return self.spec is not None and not any(i.severity == "error" for i in self.issues)


def interpret(params: InterpretCharacterParams, llm: LLMSettings, images: list[ImagePart] | None = None,
              semantic_repairs: int = 1) -> Interpretation:
    profile = get_style_profile(params.style_profile)
    provider, model = llm.for_task(TASK, params.provider)
    output_model = proposal_model(profile)
    system = system_prompt(profile)
    out = Interpretation(spec=None, issues=[], provider=provider.name, model=model)
    issues: list[Issue] | None = None
    previous: str | None = None
    for round_ in range(semantic_repairs + 1):
        user = user_prompt(params, issues, previous)
        if round_ == 0:
            out.prompt_sha256 = hashlib.sha256(f"{system}\n\n{user}".encode()).hexdigest()
        result = provider.complete(LLMRequest(model=model, system=system, user=user, output_model=output_model,
                                              max_tokens=4000, images=list(images or []),
                                              json_repairs=llm.json_repairs))
        out.calls += result.calls
        out.repair_turns += max(0, result.calls - 1) + (1 if round_ else 0)
        for k, v in result.usage.items():
            out.usage[k] = out.usage.get(k, 0) + v
        if result.stop != "ok" or result.parsed is None:
            out.stop = result.stop
            raise AIRefused(f"{provider.name} stopped with {result.stop} {result.detail}".strip())
        out.proposal = result.parsed.model_dump()
        spec = to_spec(result.parsed, params, profile)
        out.issues = validate_spec(spec, profile)
        out.spec = spec
        issues = [i for i in out.issues if i.severity == "error"]
        if not issues:
            return out
        previous = result.parsed.model_dump_json()
        log.info("%s: proposal has %d issues; asking for a repair", TASK, len(issues))
    return out


def run(sessions: sessionmaker[Session], job_id: uuid.UUID, params_raw: dict[str, Any], llm: LLMSettings,
        storage: Any, semantic_repairs: int = 1) -> dict[str, Any]:
    """Run the task for a claimed job, log the generation, return the job's result data.
    Raises ``AIRefused`` / ``ValueError`` for permanent failures; anything else may be retried."""
    params = InterpretCharacterParams.model_validate(params_raw)
    images, image_refs = load_images(storage, params.reference_image_keys)
    record = {"job_id": job_id, "task": TASK, "input_refs": {"params": params.model_dump(), "images": image_refs}}
    try:
        out = interpret(params, llm, images, semantic_repairs)
    except AIRefused as exc:
        provider, model = llm.choice_for(TASK, params.provider)
        _log(sessions, **record, provider=provider, model=model, prompt_sha256="", output=None, issues=None,
             valid=False, stop="refusal", repair_turns=0, calls=1, usage={}, error=str(exc))
        raise
    data: dict[str, Any] = {
        "valid": out.valid,
        "spec": out.spec.model_dump(mode="json") if out.spec else None,
        "issues": [i.model_dump() for i in out.issues],
        "unrepresented": (out.proposal or {}).get("unrepresented", []),
        "notes": (out.proposal or {}).get("notes", ""),
        "provider": out.provider, "model": out.model, "usage": out.usage,
    }
    if out.valid and out.spec:
        resolved = resolve_spec(out.spec)
        data["resolved_digest"] = resolved.digest()
    gen_id = _log(sessions, **record, provider=out.provider, model=out.model, prompt_sha256=out.prompt_sha256,
                  output=data["spec"], issues=data["issues"], valid=out.valid, stop=out.stop,
                  repair_turns=out.repair_turns, calls=out.calls, usage=out.usage, error=None)
    data["ai_generation_id"] = str(gen_id)
    if not out.valid:
        raise ValueError("the AI proposal still fails validation after repair: " +
                         json.dumps(data["issues"])[:1500])
    return data


def _log(sessions: sessionmaker[Session], **values: Any) -> uuid.UUID:
    with sessions.begin() as s:
        row = AIGeneration(**values)
        s.add(row)
        s.flush()
        return row.id
