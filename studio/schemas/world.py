"""Agent 3, World and Scene Builder: world.json"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from studio.ids import (
    AnchorId,
    BeatId,
    BuildRequestId,
    CharacterId,
    HexColor,
    LibraryId,
    LocationId,
    OutfitId,
    PropId,
    SceneId,
)
from studio.schemas.build_requests import BuildRequest
from studio.schemas.common import AssetStatus, SettingType, StudioModel, TimeOfDay, VersionedFile

Vec3 = tuple[float, float, float]


class Anchor(StudioModel):
    id: AnchorId
    library_anchor: str | None = None
    position_m: Vec3
    facing_deg: float = 0.0


class SetDressing(StudioModel):
    prop_id: PropId
    anchor_id: AnchorId
    offset_m: Vec3 = (0.0, 0.0, 0.0)


class KitPiecePlacement(StudioModel):
    piece_id: str
    position_m: Vec3
    rotation_deg: float = 0.0
    scale: float = Field(default=1.0, gt=0)
    anchor_id: AnchorId | None = None


class GeneratorUse(StudioModel):
    generator_id: str
    params: dict[str, float] = Field(default_factory=dict)


class ScatterUse(StudioModel):
    generator_id: str
    piece_ids: list[str] = Field(default_factory=list)
    density: float = Field(default=0.5, ge=0, le=1)
    exclude_radius_m: float = Field(default=0.0, ge=0)
    ring: dict[str, float] | None = None


class LocationAsset(StudioModel):
    status: AssetStatus
    library_id: LibraryId | None = None
    time_variant: str | None = None
    build_request_id: BuildRequestId | None = None
    # composed (kit) fields
    kit_id: str | None = None
    seed: int | None = None
    bounds_m: Vec3 | None = None
    terrain: GeneratorUse | None = None
    pieces: list[KitPiecePlacement] = Field(default_factory=list)
    scatter: list[ScatterUse] = Field(default_factory=list)
    lighting_preset: str | None = None

    @model_validator(mode="after")
    def _status_fields(self) -> "LocationAsset":
        if self.status == "library" and not self.library_id:
            raise ValueError("status 'library' needs library_id")
        if self.status == "composed" and not self.kit_id:
            raise ValueError("status 'composed' needs kit_id")
        if self.status == "build_request" and not self.build_request_id:
            raise ValueError("status 'build_request' needs build_request_id")
        if self.status == "parametric":
            raise ValueError("locations cannot be 'parametric'")
        return self


class Location(StudioModel):
    id: LocationId
    name: str
    setting_type: SettingType
    description: str = ""
    key_features: list[str] = Field(default_factory=list)
    color_palette: list[HexColor] = Field(default_factory=list)
    asset: LocationAsset
    anchors: list[Anchor] = Field(min_length=1)
    set_dressing: list[SetDressing] = Field(default_factory=list)
    inferred_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _anchors(self) -> "Location":
        ids = [a.id for a in self.anchors]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.id}: duplicate anchor ids")
        for sd in self.set_dressing:
            if sd.anchor_id not in ids:
                raise ValueError(f"{self.id}: set_dressing anchor {sd.anchor_id} unknown")
        for p in self.asset.pieces:
            if p.anchor_id and p.anchor_id not in ids:
                raise ValueError(f"{self.id}: piece anchor {p.anchor_id} unknown")
        return self

    def anchor_ids(self) -> set[str]:
        return {a.id for a in self.anchors}


class EmittedLight(StudioModel):
    color: HexColor
    energy_w: float = Field(gt=0)


class PropAsset(StudioModel):
    status: Literal["library", "build_request"]
    library_id: LibraryId | None = None
    build_request_id: BuildRequestId | None = None

    @model_validator(mode="after")
    def _status_fields(self) -> "PropAsset":
        if self.status == "library" and not self.library_id:
            raise ValueError("status 'library' needs library_id")
        if self.status == "build_request" and not self.build_request_id:
            raise ValueError("status 'build_request' needs build_request_id")
        return self


class Prop(StudioModel):
    id: PropId
    name: str
    description: str = ""
    owner_id: CharacterId | None = None
    asset: PropAsset
    emits_light: EmittedLight | None = None


class CastMember(StudioModel):
    character_id: CharacterId
    outfit_id: OutfitId
    starting_emotion: str = "neutral"
    start_anchor_id: AnchorId | None = None


class Scene(StudioModel):
    id: SceneId
    order: int = Field(ge=1)
    title: str
    beat_ids: list[BeatId] = Field(min_length=1)
    location_id: LocationId
    time_of_day: TimeOfDay
    weather: str = ""
    cast: list[CastMember] = Field(default_factory=list)
    prop_ids: list[PropId] = Field(default_factory=list)
    summary: str = ""
    dramatic_purpose: str = ""
    mood: str = ""
    estimated_duration_sec: float = Field(gt=0)

    def cast_ids(self) -> set[str]:
        return {c.character_id for c in self.cast}


class World(VersionedFile):
    locations: list[Location] = Field(min_length=1)
    props: list[Prop] = Field(default_factory=list)
    scenes: list[Scene] = Field(min_length=1)
    build_requests: list[BuildRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def _internal(self) -> "World":
        loc_ids = [l.id for l in self.locations]
        prop_ids = [p.id for p in self.props]
        scene_ids = [s.id for s in self.scenes]
        for name, ids in (("location", loc_ids), ("prop", prop_ids), ("scene", scene_ids)):
            if len(set(ids)) != len(ids):
                raise ValueError(f"duplicate {name} ids")
        orders = [s.order for s in self.scenes]
        if orders != sorted(orders) or len(set(orders)) != len(orders):
            raise ValueError("scenes must have unique, ascending order")
        br = {b.id for b in self.build_requests}
        for loc in self.locations:
            if loc.asset.build_request_id and loc.asset.build_request_id not in br:
                raise ValueError(f"{loc.id}: build request not declared")
            for sd in loc.set_dressing:
                if sd.prop_id not in prop_ids:
                    raise ValueError(f"{loc.id}: set_dressing prop {sd.prop_id} unknown")
        for prop in self.props:
            if prop.asset.build_request_id and prop.asset.build_request_id not in br:
                raise ValueError(f"{prop.id}: build request not declared")
        anchors_by_loc = {l.id: l.anchor_ids() for l in self.locations}
        for sc in self.scenes:
            if sc.location_id not in anchors_by_loc:
                raise ValueError(f"{sc.id}: unknown location {sc.location_id}")
            for pid in sc.prop_ids:
                if pid not in prop_ids:
                    raise ValueError(f"{sc.id}: unknown prop {pid}")
            for member in sc.cast:
                if member.start_anchor_id and member.start_anchor_id not in anchors_by_loc[sc.location_id]:
                    raise ValueError(f"{sc.id}: start anchor {member.start_anchor_id} not in {sc.location_id}")
        return self

    def location_by_id(self) -> dict[str, Location]:
        return {l.id: l for l in self.locations}

    def prop_by_id(self) -> dict[str, Prop]:
        return {p.id: p for p in self.props}

    def scene_by_id(self) -> dict[str, Scene]:
        return {s.id: s for s in self.scenes}
