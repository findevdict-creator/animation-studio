"""Character specification endpoints that need no Blender: profiles and validation.

Draft creation, AI interpretation and generation arrive with the jobs that back them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError

from app.core.auth import Principal, require
from app.core.errors import ApiError
from studio_contracts.character import (CharacterSpecification, Issue, ResolvedCharacter, llm_json_schema,
                                        resolve_spec, validate_spec)
from studio_contracts.rig_profile import BUILTIN_RIG_PROFILES, RigProfile
from studio_contracts.style_profile import BUILTIN_STYLE_PROFILES, StyleProfile, get_style_profile

router = APIRouter(tags=["characters"])


class ValidationOut(BaseModel):
    valid: bool
    issues: list[Issue]
    resolved: ResolvedCharacter | None = None
    digest: str | None = None


def _pydantic_issues(exc: ValidationError) -> list[Issue]:
    return [Issue(path=".".join(str(p) for p in e["loc"]) or "$", code=e["type"], message=e["msg"])
            for e in exc.errors()]


@router.post("/characters/spec/validate", response_model=ValidationOut,
             summary="Validate a character specification against its style profile")
def validate(body: dict[str, Any], _: Principal = Depends(require("asset.read"))) -> ValidationOut:
    """Always 200: an invalid spec is a normal answer here, listing every issue."""
    try:
        spec = CharacterSpecification.model_validate(body)
    except ValidationError as exc:
        return ValidationOut(valid=False, issues=_pydantic_issues(exc))
    issues = validate_spec(spec)
    if any(i.severity == "error" for i in issues):
        return ValidationOut(valid=False, issues=issues)
    resolved = resolve_spec(spec)
    return ValidationOut(valid=True, issues=issues, resolved=resolved, digest=resolved.digest())


@router.get("/style-profiles", response_model=list[StyleProfile])
def list_style_profiles(_: Principal = Depends(require("asset.read"))) -> list[StyleProfile]:
    return list(BUILTIN_STYLE_PROFILES.values())


@router.get("/style-profiles/{ref}", response_model=StyleProfile)
def get_style(ref: str, _: Principal = Depends(require("asset.read"))) -> StyleProfile:
    try:
        return get_style_profile(ref)
    except KeyError:
        raise ApiError(404, "style_profile_not_found", ref) from None


@router.get("/style-profiles/{ref}/llm-schema", summary="The closed JSON schema the AI must fill")
def get_llm_schema(ref: str, _: Principal = Depends(require("asset.read"))) -> dict:
    try:
        return llm_json_schema(get_style_profile(ref))
    except KeyError:
        raise ApiError(404, "style_profile_not_found", ref) from None


@router.get("/rig-profiles/{ref}", response_model=RigProfile)
def get_rig(ref: str, _: Principal = Depends(require("asset.read"))) -> RigProfile:
    if ref not in BUILTIN_RIG_PROFILES:
        raise ApiError(404, "rig_profile_not_found", ref)
    return BUILTIN_RIG_PROFILES[ref]
