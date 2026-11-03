"""Agent 2, Character Designer: characters.json"""

from __future__ import annotations

from pydantic import Field, model_validator

from studio.ids import BuildRequestId, CharacterId, HexColor, LibraryId, OutfitId, PropId
from studio.schemas.build_requests import BuildRequest
from studio.schemas.common import AssetStatus, Role, StudioModel, VersionedFile
from studio.schemas.manifest import RigType


class Appearance(StudioModel):
    height_class: str = ""
    build: str = ""
    skin: str = ""
    hair: str = ""
    eyes: str = ""
    distinguishing_features: list[str] = Field(default_factory=list)


class MaterialSpec(StudioModel):
    base_color: HexColor
    roughness: float = Field(default=0.5, ge=0, le=1)


class OutfitSlot(StudioModel):
    garment_id: str | None = None
    base_color: HexColor
    roughness: float = Field(default=0.5, ge=0, le=1)


class Outfit(StudioModel):
    id: OutfitId
    description: str = ""
    slots: dict[str, OutfitSlot] = Field(default_factory=dict)


class Relationship(StudioModel):
    character_id: CharacterId
    type: str
    description: str = ""


class CharacterAsset(StudioModel):
    status: AssetStatus
    library_id: LibraryId | None = None
    base_id: str | None = None
    rig_type: RigType
    height_m: float = Field(gt=0)
    proportions: dict[str, float] = Field(default_factory=dict)
    proportion_overrides: dict[str, float] = Field(default_factory=dict)
    materials: dict[str, MaterialSpec] = Field(default_factory=dict)
    hair_id: str | None = None
    build_request_id: BuildRequestId | None = None

    @model_validator(mode="after")
    def _status_fields(self) -> "CharacterAsset":
        if self.status == "library" and not self.library_id:
            raise ValueError("status 'library' needs library_id")
        if self.status == "parametric" and not self.base_id:
            raise ValueError("status 'parametric' needs base_id")
        if self.status == "build_request" and not self.build_request_id:
            raise ValueError("status 'build_request' needs build_request_id")
        if self.status == "composed":
            raise ValueError("characters cannot be 'composed'")
        return self


class Character(StudioModel):
    id: CharacterId
    name: str
    role: Role
    species: str = "human"
    age: str = ""
    gender: str = ""
    personality: list[str] = Field(default_factory=list)
    goal: str = ""
    arc: str = ""
    appearance: Appearance = Field(default_factory=Appearance)
    color_palette: list[HexColor] = Field(default_factory=list)
    default_outfit_id: OutfitId
    outfits: list[Outfit] = Field(min_length=1)
    expressions: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {"neutral": {}}
    )
    asset: CharacterAsset
    signature_prop_ids: list[PropId] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    voice_notes: str = ""
    reference_images: list[str] = Field(default_factory=list)
    design_brief: str = ""
    source_evidence: list[str] = Field(default_factory=list)
    inferred_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _outfits(self) -> "Character":
        ids = [o.id for o in self.outfits]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.id}: duplicate outfit ids")
        if self.default_outfit_id not in ids:
            raise ValueError(f"{self.id}: default_outfit_id not in outfits")
        if "neutral" not in self.expressions:
            raise ValueError(f"{self.id}: expressions must include 'neutral'")
        for name, weights in self.expressions.items():
            for key, w in weights.items():
                if not 0 <= w <= 1:
                    raise ValueError(f"{self.id}: expression {name}.{key} weight out of range")
        return self

    def outfit_ids(self) -> set[str]:
        return {o.id for o in self.outfits}


class Characters(VersionedFile):
    characters: list[Character] = Field(min_length=1)
    build_requests: list[BuildRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "Characters":
        ids = [c.id for c in self.characters]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate character ids")
        br = {b.id for b in self.build_requests}
        for c in self.characters:
            if c.asset.build_request_id and c.asset.build_request_id not in br:
                raise ValueError(f"{c.id}: build_request_id {c.asset.build_request_id} not declared")
        return self

    def by_id(self) -> dict[str, Character]:
        return {c.id: c for c in self.characters}

    def outfit_owner(self) -> dict[str, str]:
        """outfit id -> character id"""
        return {o.id: c.id for c in self.characters for o in c.outfits}
