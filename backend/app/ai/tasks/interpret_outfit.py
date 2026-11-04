"""``interpret_outfit``: a description (plus optional reference images) to a validated
``OutfitSpecification`` (SRS 7: "generate from natural language").

The same pattern as ``interpret_character``: the model fills a closed proposal (garments,
fabrics and colours are ``Literal`` values from the catalog and the style's clothing
conventions, lengths and fit are words mapped to numbers by code), code turns it into a
specification, ``validate_outfit`` checks it and one repair turn is allowed. The AI never
chooses the scope, the fit profiles or the owner (they come from the request), and it never
publishes: the result is a suggestion a person edits before a build.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model
from sqlalchemy.orm import Session, sessionmaker
from studio_contracts.character import Issue, Ref
from studio_contracts.jobs import InterpretOutfitParams
from studio_contracts.outfit import (
    GARMENTS,
    ClothingConventions,
    OutfitSpecification,
    get_conventions,
    get_garment,
    resolve_outfit,
    validate_outfit,
)

from app.ai.providers import ImagePart, LLMRequest, LLMSettings
from app.ai.tasks.interpret_character import AIRefused, _lit, _log, _Strict, load_images

log = logging.getLogger("studio.ai")
TASK = "interpret_outfit"
FIT_WORDS = {"close": 0.1, "regular": 0.4, "loose": 0.85}
LENGTH_WORDS = {"short": 0.1, "medium": 0.5, "long": 0.9}  # position inside the parameter's range
LENGTH_PARAMS = ("sleeve_length", "length", "collar", "hem", "brim", "crown", "size")


@lru_cache(maxsize=8)
def proposal_model(style_ref: str) -> type[BaseModel]:
    conv = get_conventions(style_ref)
    piece = create_model(
        "ProposedPiece", __base__=_Strict,
        garment=(_lit(g.key for g in GARMENTS), ...),
        fit=(Literal["close", "regular", "loose"], "regular"),
        length=(Literal["short", "medium", "long"] | None,
                Field(default=None, description="sleeve, leg, collar or brim length; null for the default")),
        fabric=(_lit(conv.fabrics) | None, Field(default=None, description="null for the garment's usual fabric")),
        color=(_lit(conv.colors), ...),
        trim_color=(_lit(conv.colors) | None, Field(default=None, description="cuffs, hems and collar")),
    )
    return create_model(
        "OutfitProposal", __base__=_Strict,
        name=(str, Field(min_length=1, max_length=80)),
        pieces=(list[piece], Field(min_length=1, max_length=6)),  # type: ignore[valid-type]
        unrepresented=(list[str], Field(default_factory=list, max_length=20,
                                        description="details from the description the catalog cannot show")),
        notes=(str, Field(default="", max_length=600)),
    )


def system_prompt(conv: ClothingConventions) -> str:
    catalog = "\n".join(f"- {g.key}: {g.label} ({g.slot}; fabrics {', '.join(g.fabrics)})" for g in GARMENTS)
    return f"""You design outfits for characters of a 3D cartoon series (style "{conv.style_profile}").
Turn the user's description into an outfit proposal. The outfit drives procedural garments, so you may
only choose values the schema allows. The garment catalog:
{catalog}

Rules:
- At most one garment per slot (upper, lower, footwear, headwear), plus any accessories.
- Pick the closest catalog garment, fabric and named colour. Never invent new ones; list anything the
  catalog cannot show (patterns, logos, a dress, gloves, jewellery, a specific brand) in `unrepresented`.
- `fit` is close, regular or loose. `length` is short, medium or long for the garment's own length
  (sleeve for tops, leg for shorts and trousers, shaft for footwear, brim for caps, size for bags).
- Keep the outfit suitable for a family audience. No real brands or logos.
A person reviews and edits your proposal before anything is built."""


def user_prompt(params: InterpretOutfitParams, issues: list[Issue] | None = None, previous: str | None = None) -> str:
    text = f"Outfit description:\n{params.description.strip()}"
    if params.name:
        text += f"\n\nThe outfit's name is {params.name!r}; keep it."
    if issues:
        text += ("\n\nYour previous proposal was:\n" + (previous or "") +
                 "\n\nIt failed these checks; return a corrected complete proposal:\n" +
                 "\n".join(f"- {i.path}: {i.message}" for i in issues))
    return text


def to_spec(proposal: BaseModel, params: InterpretOutfitParams) -> OutfitSpecification:
    p = proposal.model_dump()
    style_id, _, style_version = params.style_profile.partition("@")
    pieces = []
    for prop in p["pieces"]:
        g = get_garment(prop["garment"])
        values: dict[str, float] = {}
        if "ease" in g.params:
            d = g.params["ease"]
            values["ease"] = round(max(d.min, min(d.max, FIT_WORDS[prop["fit"]])), 3)
        if prop["length"]:
            name = next((n for n in LENGTH_PARAMS if n in g.params), None)
            if name:
                d = g.params[name]
                values[name] = round(d.min + (d.max - d.min) * LENGTH_WORDS[prop["length"]], 3)
        material = {"color": prop["color"]}
        if prop["fabric"]:
            material["fabric"] = prop["fabric"]
        if prop["trim_color"] and g.trim:
            material["trim_color"] = prop["trim_color"]
        pieces.append({"slot": g.slot, "garment": {"id": g.id, "version": g.version}, "params": values,
                       "material": material})
    return OutfitSpecification(
        name=params.name or p["name"], description=params.description[:2000],
        style_profile=Ref(id=style_id, version=style_version or "1.0"), scope=params.scope,
        owner_character=params.owner_character, fit_profiles=list(params.fit_profiles), pieces=pieces,
        reference_images=list(params.reference_image_keys))


@dataclass
class Interpretation:
    spec: OutfitSpecification | None
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


def interpret(params: InterpretOutfitParams, llm: LLMSettings, images: list[ImagePart] | None = None,
              semantic_repairs: int = 1) -> Interpretation:
    conv = get_conventions(params.style_profile)
    provider, model = llm.for_task(TASK, params.provider)
    output_model = proposal_model(conv.style_profile)
    system = system_prompt(conv)
    out = Interpretation(spec=None, issues=[], provider=provider.name, model=model)
    issues: list[Issue] | None = None
    previous: str | None = None
    for round_ in range(semantic_repairs + 1):
        user = user_prompt(params, issues, previous)
        if round_ == 0:
            out.prompt_sha256 = hashlib.sha256(f"{system}\n\n{user}".encode()).hexdigest()
        result = provider.complete(LLMRequest(model=model, system=system, user=user, output_model=output_model,
                                              max_tokens=3000, images=list(images or []),
                                              json_repairs=llm.json_repairs))
        out.calls += result.calls
        out.repair_turns += max(0, result.calls - 1) + (1 if round_ else 0)
        for k, v in result.usage.items():
            out.usage[k] = out.usage.get(k, 0) + v
        if result.stop != "ok" or result.parsed is None:
            out.stop = result.stop
            raise AIRefused(f"{provider.name} stopped with {result.stop} {result.detail}".strip())
        out.proposal = result.parsed.model_dump()
        spec = to_spec(result.parsed, params)
        out.issues = validate_outfit(spec)
        out.spec = spec
        issues = [i for i in out.issues if i.severity == "error"]
        if not issues:
            return out
        previous = result.parsed.model_dump_json()
        log.info("%s: proposal has %d issues; asking for a repair", TASK, len(issues))
    return out


def run(sessions: sessionmaker[Session], job_id: uuid.UUID, params_raw: dict[str, Any], llm: LLMSettings,
        storage: Any, semantic_repairs: int = 1) -> dict[str, Any]:
    params = InterpretOutfitParams.model_validate(params_raw)
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
        data["resolved_digest"] = resolve_outfit(out.spec).digest()
    gen_id = _log(sessions, **record, provider=out.provider, model=out.model, prompt_sha256=out.prompt_sha256,
                  output=data["spec"], issues=data["issues"], valid=out.valid, stop=out.stop,
                  repair_turns=out.repair_turns, calls=out.calls, usage=out.usage, error=None)
    data["ai_generation_id"] = str(gen_id)
    if not out.valid:
        raise ValueError("the AI proposal still fails validation after repair: " + json.dumps(data["issues"])[:1500])
    return data
