"""Outfit specification endpoints that need no Blender: the garment catalog, the clothing
conventions of a style and validation (SRS 7)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError
from studio_contracts.character import Issue
from studio_contracts.outfit import (
    GARMENTS,
    ClothingConventions,
    GarmentDef,
    OutfitSpecification,
    ResolvedOutfit,
    fit_profiles,
    get_conventions,
    resolve_outfit,
    validate_outfit,
)
from studio_contracts.style_profile import get_style_profile

from app.api.v1.characters import _pydantic_issues
from app.core.auth import Principal, require
from app.core.errors import ApiError

router = APIRouter(tags=["outfits"])


class OutfitValidationOut(BaseModel):
    valid: bool
    issues: list[Issue]
    resolved: ResolvedOutfit | None = None
    digest: str | None = None


class ConventionsOut(ClothingConventions):
    fit_profiles: list[str]


@router.post("/outfits/spec/validate", response_model=OutfitValidationOut,
             summary="Validate an outfit specification against the garment catalog and its style")
def validate(body: dict[str, Any], _: Principal = Depends(require("asset.read"))) -> OutfitValidationOut:
    """Always 200: an invalid spec is a normal answer here, listing every issue."""
    try:
        spec = OutfitSpecification.model_validate(body)
    except ValidationError as exc:
        return OutfitValidationOut(valid=False, issues=_pydantic_issues(exc))
    issues = validate_outfit(spec)
    if any(i.severity == "error" for i in issues):
        return OutfitValidationOut(valid=False, issues=issues)
    resolved = resolve_outfit(spec)
    return OutfitValidationOut(valid=True, issues=issues, resolved=resolved, digest=resolved.digest())


@router.get("/garments", response_model=list[GarmentDef], summary="The garment catalog (procedural stand-ins)")
def list_garments(_: Principal = Depends(require("asset.read"))) -> list[GarmentDef]:
    return list(GARMENTS)


@router.get("/style-profiles/{ref}/clothing", response_model=ConventionsOut,
            summary="Colours, fabrics, fit thresholds and fit profiles of a style")
def get_clothing(ref: str, _: Principal = Depends(require("asset.read"))) -> ConventionsOut:
    try:
        conv = get_conventions(ref)
    except KeyError as exc:
        raise ApiError(404, "clothing_conventions_not_found", str(exc.args[0])) from None
    return ConventionsOut(**conv.model_dump(), fit_profiles=fit_profiles(get_style_profile(ref)))
