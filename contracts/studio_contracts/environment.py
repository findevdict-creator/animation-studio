"""Environments and props (SRS 12, SRS 30 Phase 7, architecture 5.3).

A **prop** (``PROP-001``) is its own asset with its own versions: one object built by code
from a catalog item (``PROPD-CHAIR@0.1``) with parameters, finish and colours. Scenes place
published prop versions; environments are dressed with catalog items directly.

An **environment** (``ENV-001``) is a set built by code from a template (``ENVD-LIVING_ROOM``):
a room shell with doors and windows, or an outdoor ground; floor and wall materials; a
lighting preset; set dressing (catalog props placed in it); named marks (where characters
start, sit or interact); and camera marks. Its layout is checked here, in pure Python, so the
API, the AI and Blender agree: dressing inside the walls and not overlapping, openings that
fit their walls, a door that is not blocked, marks on free floor. Blender only ever sees
``ResolvedEnvironment`` and measures the built set again.

Coordinates: metres, the floor centre at the origin, +X east, +Y north, +Z up. Characters
face -Y (south) by default, toward the default cameras. A room's south wall is a
*cutaway*: it is built and casts shadows but the cameras see through it, as on a stage set.

The catalogs are **procedural stand-ins** (``0.x``), in the same way the garment and
accessory catalogs are (decision D11): simple shapes with toon materials, not modelled sets.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from studio_contracts.character import Issue, Ref
from studio_contracts.outfit import get_conventions
from studio_contracts.style_profile import HEX, get_style_profile

PROP_SCHEMA = "prop_spec/1"
ENV_SCHEMA = "environment_spec/1"
CATALOG_VERSION = "0.1"
#: radius a standing character keeps clear around itself on the floor, metres
CHARACTER_RADIUS_M = 0.22
#: gap kept between dressing items, and between dressing and walls
CLEARANCE_M = 0.02

PropCategory = Literal["furniture", "household", "toy", "nature", "vehicle", "food"]
Wall = Literal["north", "south", "east", "west"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParamDef(_Frozen):
    min: float
    max: float
    default: float
    description: str


def _p(lo: float, hi: float, default: float, text: str) -> ParamDef:
    return ParamDef(min=lo, max=hi, default=default, description=text)


# ------------------------------------------------------------------ prop catalog
class Surface(_Frozen):
    """A place on a prop: a seat or a top that something can stand on, at a height that
    scales with the prop's height."""

    name: str  # seat, top, shelf
    kind: Literal["seat", "top"]
    height_share: float = Field(gt=0, le=1)


class PropDef(_Frozen):
    id: str = Field(pattern=r"^PROPD-[A-Z0-9_]+$")
    version: str
    key: str
    label: str
    category: PropCategory
    #: width (x), depth (y), height (z) at size 0.5, metres
    base_size: tuple[float, float, float]
    params: dict[str, ParamDef] = Field(default_factory=dict)
    finishes: tuple[str, ...]
    default_finish: str
    default_color: str
    accent: str | None = None
    default_accent: str | None = None
    #: characters walk around it; false for things on a table or so low they are stepped over
    solid: bool = True
    #: goes on the floor, or on a ``top`` surface of another item
    rests_on: Literal["floor", "top"] = "floor"
    surfaces: tuple[Surface, ...] = ()
    #: outdoor only (a tree), indoor only (a bed), or either
    setting: Literal["indoor", "outdoor", "any"] = "any"

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def size(self, params: dict[str, float]) -> tuple[float, float, float]:
        """Width, depth, height for these parameters. ``size`` scales everything 0.8..1.2;
        ``width`` / ``height`` stretch one axis 0.75..1.25."""
        p = {k: params.get(k, d.default) for k, d in self.params.items()}
        s = 0.8 + 0.4 * p.get("size", 0.5)
        w = 0.75 + 0.5 * p.get("width", 0.5)
        h = 0.75 + 0.5 * p.get("height", 0.5)
        bw, bd, bh = self.base_size
        return (round(bw * s * w, 4), round(bd * s, 4), round(bh * s * h, 4))


_SIZE = _p(0.0, 1.0, 0.5, "overall size: 0 is 80 %, 1 is 120 %")
_WIDTH = _p(0.0, 1.0, 0.5, "width: 0 is narrow, 1 is wide")
_HEIGHT = _p(0.0, 1.0, 0.5, "height: 0 is low, 1 is tall")

PROPS: tuple[PropDef, ...] = (
    PropDef(id="PROPD-TABLE", version=CATALOG_VERSION, key="table", label="Table", category="furniture",
            base_size=(1.2, 0.75, 0.72), params={"size": _SIZE, "width": _WIDTH, "height": _HEIGHT},
            finishes=("wood", "painted"), default_finish="wood", default_color="#A8754A",
            surfaces=(Surface(name="top", kind="top", height_share=1.0),), setting="indoor"),
    PropDef(id="PROPD-CHAIR", version=CATALOG_VERSION, key="chair", label="Chair", category="furniture",
            base_size=(0.45, 0.47, 0.88), params={"size": _SIZE, "height": _HEIGHT},
            finishes=("wood", "painted", "plastic"), default_finish="wood", default_color="#9C6A41",
            surfaces=(Surface(name="seat", kind="seat", height_share=0.5),)),
    PropDef(id="PROPD-DESK", version=CATALOG_VERSION, key="desk", label="School desk", category="furniture",
            base_size=(0.7, 0.5, 0.68), params={"size": _SIZE, "width": _WIDTH},
            finishes=("wood", "painted"), default_finish="painted", default_color="#C9B48A", accent="frame",
            default_accent="#5A6672", surfaces=(Surface(name="top", kind="top", height_share=1.0),),
            setting="indoor"),
    PropDef(id="PROPD-SOFA", version=CATALOG_VERSION, key="sofa", label="Sofa", category="furniture",
            base_size=(1.9, 0.85, 0.85), params={"size": _SIZE, "width": _WIDTH},
            finishes=("fabric", "leather"), default_finish="fabric", default_color="teal", accent="cushions",
            default_accent="cream", surfaces=(Surface(name="seat", kind="seat", height_share=0.5),),
            setting="indoor"),
    PropDef(id="PROPD-BED", version=CATALOG_VERSION, key="bed", label="Bed", category="furniture",
            base_size=(1.0, 2.0, 0.55), params={"size": _SIZE, "width": _WIDTH},
            finishes=("wood", "painted"), default_finish="wood", default_color="#B88A5C", accent="covers",
            default_accent="sky_blue", surfaces=(Surface(name="top", kind="top", height_share=0.8),),
            setting="indoor"),
    PropDef(id="PROPD-BOOKSHELF", version=CATALOG_VERSION, key="bookshelf", label="Bookshelf", category="furniture",
            base_size=(0.9, 0.32, 1.8), params={"size": _SIZE, "width": _WIDTH, "height": _HEIGHT},
            finishes=("wood", "painted"), default_finish="wood", default_color="#8A5A36", accent="books",
            default_accent="red", setting="indoor"),
    PropDef(id="PROPD-BOOKS", version=CATALOG_VERSION, key="books", label="Stack of books", category="household",
            base_size=(0.26, 0.2, 0.14), params={"size": _SIZE, "height": _HEIGHT},
            finishes=("paper",), default_finish="paper", default_color="royal_blue", accent="pages",
            default_accent="cream", solid=False, rests_on="top"),
    PropDef(id="PROPD-LAMP", version=CATALOG_VERSION, key="lamp", label="Floor lamp", category="household",
            base_size=(0.4, 0.4, 1.6), params={"size": _SIZE, "height": _HEIGHT},
            finishes=("metal", "painted"), default_finish="metal", default_color="#2B2B30", accent="shade",
            default_accent="cream", setting="indoor"),
    PropDef(id="PROPD-BALL", version=CATALOG_VERSION, key="ball", label="Ball", category="toy",
            base_size=(0.24, 0.24, 0.24), params={"size": _SIZE}, finishes=("rubber",), default_finish="rubber",
            default_color="red", accent="stripe", default_accent="white", solid=False),
    PropDef(id="PROPD-TOY_CAR", version=CATALOG_VERSION, key="toy_car", label="Toy car", category="vehicle",
            base_size=(0.3, 0.16, 0.13), params={"size": _SIZE}, finishes=("plastic",), default_finish="plastic",
            default_color="yellow", accent="windows", default_accent="sky_blue", solid=False),
    PropDef(id="PROPD-APPLE", version=CATALOG_VERSION, key="apple", label="Apple", category="food",
            base_size=(0.08, 0.08, 0.09), params={"size": _SIZE}, finishes=("fruit",), default_finish="fruit",
            default_color="red", accent="leaf", default_accent="lime", solid=False, rests_on="top"),
    PropDef(id="PROPD-TREE", version=CATALOG_VERSION, key="tree", label="Tree", category="nature",
            base_size=(2.2, 2.2, 4.0), params={"size": _SIZE, "height": _HEIGHT}, finishes=("bark",),
            default_finish="bark", default_color="#6B4A2E", accent="leaves", default_accent="forest_green",
            setting="outdoor"),
    PropDef(id="PROPD-BENCH", version=CATALOG_VERSION, key="bench", label="Park bench", category="furniture",
            base_size=(1.6, 0.6, 0.82), params={"size": _SIZE, "width": _WIDTH}, finishes=("wood", "painted"),
            default_finish="wood", default_color="#9C6A41", accent="frame", default_accent="#2B2B30",
            surfaces=(Surface(name="seat", kind="seat", height_share=0.55),)),
    PropDef(id="PROPD-CAR", version=CATALOG_VERSION, key="car", label="Car", category="vehicle",
            base_size=(1.8, 4.0, 1.5), params={"size": _SIZE}, finishes=("paint",), default_finish="paint",
            default_color="royal_blue", accent="windows", default_accent="#BFD6E6", setting="outdoor"),
)
PROP_CATALOG: dict[str, PropDef] = {p.ref: p for p in PROPS}
PROP_BY_KEY: dict[str, PropDef] = {p.key: p for p in PROPS}


def get_prop_def(ref: str) -> PropDef:
    """``PROPD-CHAIR@0.1``, or a catalog key such as ``chair``."""
    if ref in PROP_CATALOG:
        return PROP_CATALOG[ref]
    if ref in PROP_BY_KEY:
        return PROP_BY_KEY[ref]
    raise KeyError(f"no prop {ref!r} in catalog {CATALOG_VERSION}")


# ------------------------------------------------------------------ finishes and lighting
class Finish(_Frozen):
    roughness: float = Field(ge=0, le=1)
    specular: float = Field(ge=0, le=1)


FINISHES: dict[str, Finish] = {
    "wood": Finish(roughness=0.6, specular=0.3), "painted": Finish(roughness=0.45, specular=0.4),
    "plastic": Finish(roughness=0.35, specular=0.5), "fabric": Finish(roughness=0.95, specular=0.1),
    "leather": Finish(roughness=0.5, specular=0.4), "paper": Finish(roughness=0.9, specular=0.1),
    "metal": Finish(roughness=0.3, specular=0.8), "rubber": Finish(roughness=0.7, specular=0.2),
    "fruit": Finish(roughness=0.4, specular=0.5), "bark": Finish(roughness=1.0, specular=0.05),
    "paint": Finish(roughness=0.25, specular=0.7),
    # floors, walls, ground
    "floorboards": Finish(roughness=0.55, specular=0.3), "carpet": Finish(roughness=1.0, specular=0.05),
    "tiles": Finish(roughness=0.3, specular=0.5), "plaster": Finish(roughness=0.9, specular=0.1),
    "wallpaper": Finish(roughness=0.85, specular=0.1), "grass": Finish(roughness=1.0, specular=0.05),
    "asphalt": Finish(roughness=0.95, specular=0.1), "paving": Finish(roughness=0.85, specular=0.15),
}


class LightingPreset(_Frozen):
    key: str
    label: str
    #: sun (or moon) strength, elevation and heading in degrees, colour
    sun_energy: float
    sun_elevation_deg: float
    sun_heading_deg: float
    sun_color: str
    #: world (sky) colour and strength
    sky_color: str
    sky_strength: float
    #: indoor ceiling lights: strength per light (0: none) and colour
    ceiling_energy_w: float
    ceiling_color: str
    #: soft fill from the camera side, as a share of the sun
    fill_share: float = Field(ge=0, le=1)


LIGHTING: dict[str, LightingPreset] = {p.key: p for p in (
    LightingPreset(key="day", label="Daylight", sun_energy=3.0, sun_elevation_deg=55, sun_heading_deg=-30,
                   sun_color="#FFF6E8", sky_color="#BFD8F2", sky_strength=1.0, ceiling_energy_w=0,
                   ceiling_color="#FFF1DC", fill_share=0.35),
    LightingPreset(key="overcast", label="Overcast", sun_energy=1.2, sun_elevation_deg=70, sun_heading_deg=-20,
                   sun_color="#F2F2F2", sky_color="#C9CED4", sky_strength=1.4, ceiling_energy_w=0,
                   ceiling_color="#FFF1DC", fill_share=0.6),
    LightingPreset(key="evening", label="Golden hour", sun_energy=2.4, sun_elevation_deg=12, sun_heading_deg=-70,
                   sun_color="#FFB46B", sky_color="#E8B48C", sky_strength=0.7, ceiling_energy_w=0,
                   ceiling_color="#FFD9A8", fill_share=0.3),
    LightingPreset(key="night", label="Night", sun_energy=0.25, sun_elevation_deg=40, sun_heading_deg=30,
                   sun_color="#9FB4E8", sky_color="#1C2540", sky_strength=0.35, ceiling_energy_w=0,
                   ceiling_color="#FFD9A8", fill_share=0.5),
    LightingPreset(key="indoor_warm", label="Indoor, lamps on", sun_energy=0.8, sun_elevation_deg=35,
                   sun_heading_deg=-40, sun_color="#FFF1DC", sky_color="#D9D2C6", sky_strength=0.5,
                   ceiling_energy_w=180, ceiling_color="#FFE2B8", fill_share=0.4),
    LightingPreset(key="indoor_day", label="Indoor, daylight", sun_energy=2.2, sun_elevation_deg=40,
                   sun_heading_deg=-35, sun_color="#FFF6E8", sky_color="#DCE6EF", sky_strength=0.9,
                   ceiling_energy_w=60, ceiling_color="#FFF1DC", fill_share=0.45),
)}


# ------------------------------------------------------------------ environment templates
class Placement(_Frozen):
    """A dressing item in a template, or in a specification."""

    prop: str  # catalog key or ref
    at: tuple[float, float]
    rotation_deg: float = 0.0
    params: dict[str, float] = Field(default_factory=dict)


class EnvironmentDef(_Frozen):
    id: str = Field(pattern=r"^ENVD-[A-Z0-9_]+$")
    version: str
    key: str
    label: str
    kind: Literal["room", "outdoor"]
    #: width (x), depth (y), height (z); outdoor height is unused
    default_size: tuple[float, float, float]
    min_size: tuple[float, float, float]
    max_size: tuple[float, float, float]
    floor_finishes: tuple[str, ...]
    default_floor: str
    default_floor_color: str
    wall_finishes: tuple[str, ...] = ()
    default_wall: str | None = None
    default_wall_color: str | None = None
    default_lighting: str
    #: dressing when the specification gives none, placed as shares of the size (-0.5..0.5)
    dressing: tuple[Placement, ...] = ()
    #: a door in the north wall unless the specification says otherwise (rooms)
    door: bool = True
    #: where the door is along the north wall, as a share -0.5..0.5
    door_offset: float = 0.32
    windows: int = 1

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


ENVIRONMENTS: tuple[EnvironmentDef, ...] = (
    EnvironmentDef(id="ENVD-LIVING_ROOM", version=CATALOG_VERSION, key="living_room", label="Living room", kind="room",
                   default_size=(6.0, 5.0, 2.7), min_size=(3.5, 3.0, 2.4), max_size=(10.0, 8.0, 3.5),
                   floor_finishes=("floorboards", "carpet"), default_floor="floorboards",
                   default_floor_color="#B98A5E", wall_finishes=("plaster", "wallpaper"), default_wall="wallpaper",
                   default_wall_color="#E8DCC4", default_lighting="indoor_day", door_offset=-0.32,
                   dressing=(Placement(prop="sofa", at=(-0.05, 0.36)), Placement(prop="table", at=(-0.05, 0.14),
                                                                                params={"height": 0.0, "size": 0.2}),
                             Placement(prop="lamp", at=(0.36, 0.38)), Placement(prop="bookshelf", at=(-0.43, 0.2),
                                                                                rotation_deg=-90),
                             Placement(prop="ball", at=(0.25, -0.15)))),
    EnvironmentDef(id="ENVD-BEDROOM", version=CATALOG_VERSION, key="bedroom", label="Bedroom", kind="room",
                   default_size=(4.5, 4.0, 2.6), min_size=(3.0, 3.0, 2.4), max_size=(8.0, 7.0, 3.2),
                   floor_finishes=("floorboards", "carpet"), default_floor="carpet", default_floor_color="#9DB4C9",
                   wall_finishes=("plaster", "wallpaper"), default_wall="plaster", default_wall_color="#F0E6D6",
                   default_lighting="indoor_warm",
                   dressing=(Placement(prop="bed", at=(-0.3, 0.22)), Placement(prop="bookshelf", at=(0.1, 0.42)),
                             Placement(prop="toy_car", at=(0.15, -0.1)))),
    EnvironmentDef(id="ENVD-CLASSROOM", version=CATALOG_VERSION, key="classroom", label="Classroom", kind="room",
                   default_size=(8.0, 7.0, 3.0), min_size=(5.0, 5.0, 2.6), max_size=(12.0, 10.0, 4.0),
                   floor_finishes=("tiles", "floorboards"), default_floor="tiles", default_floor_color="#C8C3B5",
                   wall_finishes=("plaster",), default_wall="plaster", default_wall_color="#E9EEE4",
                   default_lighting="indoor_day", windows=2,
                   # two rows of four desks with a centre aisle, chairs on the south side
                   dressing=tuple(Placement(prop="desk", at=(x, y)) for y in (0.05, 0.25)
                                  for x in (-0.3, -0.12, 0.12, 0.3)) +
                   tuple(Placement(prop="chair", at=(x, y - 0.09), rotation_deg=180) for y in (0.05, 0.25)
                         for x in (-0.3, -0.12, 0.12, 0.3)) +
                   (Placement(prop="table", at=(0.0, 0.42), params={"size": 0.6}),
                    Placement(prop="bookshelf", at=(-0.45, 0.38), rotation_deg=-90))),
    EnvironmentDef(id="ENVD-PARK", version=CATALOG_VERSION, key="park", label="Park", kind="outdoor",
                   default_size=(16.0, 12.0, 0.0), min_size=(8.0, 6.0, 0.0), max_size=(40.0, 30.0, 0.0),
                   floor_finishes=("grass",), default_floor="grass", default_floor_color="#6FA24A",
                   default_lighting="day", door=False, windows=0,
                   dressing=(Placement(prop="tree", at=(-0.3, 0.3)), Placement(prop="tree", at=(0.32, 0.35),
                                                                               params={"height": 0.8}),
                             Placement(prop="bench", at=(0.0, 0.22)), Placement(prop="ball", at=(0.12, -0.1)))),
    EnvironmentDef(id="ENVD-STREET", version=CATALOG_VERSION, key="street", label="Street", kind="outdoor",
                   default_size=(20.0, 12.0, 0.0), min_size=(10.0, 8.0, 0.0), max_size=(50.0, 30.0, 0.0),
                   floor_finishes=("paving", "asphalt"), default_floor="paving", default_floor_color="#B7B2A8",
                   default_lighting="day", door=False, windows=0,
                   dressing=(Placement(prop="car", at=(-0.25, 0.3), rotation_deg=90),
                             Placement(prop="tree", at=(0.3, 0.38)), Placement(prop="bench", at=(0.1, 0.3)))),
)
ENVIRONMENT_CATALOG: dict[str, EnvironmentDef] = {e.ref: e for e in ENVIRONMENTS}
ENVIRONMENT_BY_KEY: dict[str, EnvironmentDef] = {e.key: e for e in ENVIRONMENTS}


def get_environment_def(ref: str) -> EnvironmentDef:
    if ref in ENVIRONMENT_CATALOG:
        return ENVIRONMENT_CATALOG[ref]
    if ref in ENVIRONMENT_BY_KEY:
        return ENVIRONMENT_BY_KEY[ref]
    raise KeyError(f"no environment template {ref!r} in catalog {CATALOG_VERSION}")


# ------------------------------------------------------------------ 2D layout geometry
Rect = tuple[float, float, float, float, float]  # centre x, centre y, width, depth, rotation degrees


def corners(r: Rect) -> list[tuple[float, float]]:
    cx, cy, w, d, rot = r
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in ((-w / 2, -d / 2), (w / 2, -d / 2),
                                                                    (w / 2, d / 2), (-w / 2, d / 2))]


def overlap(a: Rect, b: Rect, gap: float = 0.0) -> bool:
    """Two rotated rectangles overlap (closer than ``gap``), by separating axes."""
    ca = corners((a[0], a[1], a[2] + gap, a[3] + gap, a[4]))
    cb = corners(b)
    for poly in (ca, cb):
        for i in range(4):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % 4]
            nx, ny = y2 - y1, x1 - x2
            pa = [nx * x + ny * y for x, y in ca]
            pb = [nx * x + ny * y for x, y in cb]
            if max(pa) < min(pb) or max(pb) < min(pa):
                return False
    return True


def contains(r: Rect, x: float, y: float, margin: float = 0.0) -> bool:
    cx, cy, w, d, rot = r
    c, s = math.cos(math.radians(-rot)), math.sin(math.radians(-rot))
    lx, ly = (x - cx) * c - (y - cy) * s, (x - cx) * s + (y - cy) * c
    return abs(lx) <= w / 2 + margin and abs(ly) <= d / 2 + margin


def inside_bounds(r: Rect, width: float, depth: float, margin: float = 0.0) -> bool:
    return all(abs(x) <= width / 2 - margin + 1e-9 and abs(y) <= depth / 2 - margin + 1e-9 for x, y in corners(r))


# ------------------------------------------------------------------ specifications
class PropMaterial(Model):
    finish: str | None = None
    color: str | None = None  # preset name or #RRGGBB
    accent_color: str | None = None


class PropSpecification(Model):
    schema_version: Literal["prop_spec/1"] = PROP_SCHEMA
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    style_profile: Ref = Field(default_factory=lambda: Ref(id="family_cartoon", version="1.0"))
    item: Ref
    params: dict[str, float] = Field(default_factory=dict)
    material: PropMaterial = Field(default_factory=PropMaterial)
    seed: int = Field(default=0, ge=0, lt=2**31)


class Opening(Model):
    kind: Literal["door", "window"]
    wall: Wall
    #: centre along the wall, as a share from -0.5 (west or south end) to 0.5
    offset: float = Field(default=0.0, ge=-0.5, le=0.5)
    width_m: float | None = Field(default=None, gt=0.3, le=4.0)


class Dressing(Model):
    """A catalog prop placed in the set; ``at`` is in metres from the floor centre. With
    ``on`` it stands on the top of the dressing item with that id instead of the floor."""

    id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    prop: Ref
    at: tuple[float, float] = (0.0, 0.0)
    rotation_deg: float = Field(default=0.0, ge=-360, le=360)
    params: dict[str, float] = Field(default_factory=dict)
    material: PropMaterial = Field(default_factory=PropMaterial)
    on: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")


class Mark(Model):
    """A named spot: where a character starts (``spawn``), sits (``seat``, on a dressing
    item's seat) or does something (``interaction``)."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    kind: Literal["spawn", "seat", "interaction"] = "spawn"
    at: tuple[float, float] = (0.0, 0.0)
    facing_deg: float = Field(default=0.0, ge=-360, le=360, description="0 faces south (-Y), the default cameras")
    #: for a seat: the dressing item it is on
    item: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")


class CameraMark(Model):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    at: tuple[float, float, float]
    look_at: tuple[float, float, float]
    lens_mm: float = Field(default=28.0, ge=12, le=120)


class EnvironmentMaterials(Model):
    floor_finish: str | None = None
    floor_color: str | None = None
    wall_finish: str | None = None
    wall_color: str | None = None


class EnvironmentSpecification(Model):
    schema_version: Literal["environment_spec/1"] = ENV_SCHEMA
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    style_profile: Ref = Field(default_factory=lambda: Ref(id="family_cartoon", version="1.0"))
    template: Ref
    #: width (x), depth (y), height (z, rooms); the template's when omitted
    size: tuple[float, float, float] | None = None
    materials: EnvironmentMaterials = Field(default_factory=EnvironmentMaterials)
    lighting: str | None = Field(default=None, description="a lighting preset; the template's when omitted")
    #: doors and windows (rooms); the template's when omitted
    openings: list[Opening] | None = Field(default=None, max_length=12)
    #: set dressing; the template's when omitted, none with []
    dressing: list[Dressing] | None = Field(default=None, max_length=60)
    #: marks; a ``centre`` spawn mark is added when none is given
    marks: list[Mark] = Field(default_factory=list, max_length=32)
    #: camera marks; ``wide`` and ``front`` are added when none is given
    cameras: list[CameraMark] = Field(default_factory=list, max_length=12)
    seed: int = Field(default=0, ge=0, lt=2**31)


# ------------------------------------------------------------------ resolved (what Blender sees)
class ResolvedProp(_Frozen):
    name: str
    style_profile: str
    catalog: str
    item: str  # PROPD-CHAIR@0.1
    key: str
    category: str
    params: dict[str, float]
    size: tuple[float, float, float]
    finish: str
    color: str
    accent_color: str | None
    roughness: float
    specular: float
    solid: bool
    rests_on: str
    surfaces: tuple[Surface, ...]
    seed: int

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()

    @property
    def object_name(self) -> str:
        return f"PROP_{self.key}"

    def surface_z(self, name: str) -> float | None:
        s = next((s for s in self.surfaces if s.name == name), None)
        return round(self.size[2] * s.height_share, 4) if s else None


class ResolvedDressing(_Frozen):
    id: str
    prop: ResolvedProp
    at: tuple[float, float]
    #: height it stands at: 0 on the floor, the top of ``on`` otherwise
    z: float
    rotation_deg: float
    on: str | None

    @property
    def footprint(self) -> Rect:
        return (self.at[0], self.at[1], self.prop.size[0], self.prop.size[1], self.rotation_deg)


class ResolvedOpening(_Frozen):
    kind: str
    wall: str
    #: centre along the wall in metres from its middle, width, and sill / head heights
    offset_m: float
    width_m: float
    sill_m: float
    head_m: float


class ResolvedMark(_Frozen):
    name: str
    kind: str
    at: tuple[float, float, float]
    facing_deg: float
    item: str | None


class ResolvedEnvironment(_Frozen):
    name: str
    style_profile: str
    catalog: str
    template: str  # ENVD-LIVING_ROOM@0.1
    key: str
    kind: str
    size: tuple[float, float, float]
    floor_finish: str
    floor_color: str
    floor_roughness: float
    wall_finish: str | None
    wall_color: str | None
    wall_roughness: float | None
    lighting: LightingPreset
    openings: tuple[ResolvedOpening, ...]
    dressing: tuple[ResolvedDressing, ...]
    marks: tuple[ResolvedMark, ...]
    cameras: tuple[CameraMark, ...]
    seed: int

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()

    @property
    def collection_name(self) -> str:
        return f"ENV_{self.key}"

    def walkable(self, x: float, y: float, radius: float = CHARACTER_RADIUS_M) -> bool:
        """A character of ``radius`` can stand at (x, y): inside the walls, clear of every
        solid floor item."""
        w, d = self.size[0], self.size[1]
        if abs(x) > w / 2 - radius or abs(y) > d / 2 - radius:
            return False
        return not any(it.prop.solid and it.on is None and contains(it.footprint, x, y, radius) for it in self.dressing)

    def mark(self, name: str) -> ResolvedMark | None:
        return next((m for m in self.marks if m.name == name), None)

    def layout(self) -> dict:
        """What a scene and the web need without the files: size, kind, the floor footprints
        of solid dressing, marks and camera marks (``environment_versions.layout``)."""
        return {"kind": self.kind, "size": list(self.size), "lighting": self.lighting.key,
                "solids": [[it.id, *it.footprint] for it in self.dressing if it.prop.solid and it.on is None],
                "tops": [[it.id, *it.footprint, it.z + (it.prop.surface_z("top") or 0.0)] for it in self.dressing
                         if it.prop.surface_z("top") is not None],
                "seats": [[it.id, *it.footprint, it.prop.surface_z("seat")] for it in self.dressing
                          if it.prop.surface_z("seat") is not None],
                "marks": [m.model_dump(mode="json") for m in self.marks],
                "cameras": [c.model_dump(mode="json") for c in self.cameras]}


class InvalidSpecification(ValueError):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("; ".join(f"{i.path}: {i.message}" for i in issues if i.severity == "error"))


def _color(value: str, presets: dict[str, str], path: str, issues: list[Issue]) -> str | None:
    if value in presets:
        return presets[value]
    if re.fullmatch(HEX, value):
        return value.upper()
    issues.append(Issue(path=path, code="unknown_color",
                        message=f"{value!r} is neither #RRGGBB nor one of {sorted(presets)}"))
    return None


def _prop_issues(item: PropDef, params: dict[str, float], m: PropMaterial, colors: dict[str, str], path: str,
                 issues: list[Issue]) -> None:
    for name, value in params.items():
        d = item.params.get(name)
        if d is None:
            issues.append(Issue(path=f"{path}params.{name}", code="unknown_param",
                                message=f"a {item.label} has parameters {sorted(item.params)}"))
        elif not d.min <= value <= d.max:
            issues.append(Issue(path=f"{path}params.{name}", code="out_of_range",
                                message=f"{value} is outside {d.min}..{d.max}"))
    if m.finish is not None and m.finish not in item.finishes:
        issues.append(Issue(path=f"{path}material.finish", code="not_for_item",
                            message=f"a {item.label} is made of {list(item.finishes)}"))
    if m.color is not None:
        _color(m.color, colors, f"{path}material.color", issues)
    if m.accent_color is not None:
        if item.accent is None:
            issues.append(Issue(path=f"{path}material.accent_color", code="not_supported", severity="warning",
                                message=f"a {item.label} has no second colour; it is ignored"))
        else:
            _color(m.accent_color, colors, f"{path}material.accent_color", issues)


def _resolve_prop(item: PropDef, name: str, style: str, params: dict[str, float], m: PropMaterial,
                  colors: dict[str, str], seed: int) -> ResolvedProp:
    full = {n: float(params.get(n, d.default)) for n, d in item.params.items()}
    finish = m.finish or item.default_finish
    f = FINISHES[finish]
    sink: list[Issue] = []
    color = _color(m.color or item.default_color, colors, "", sink) or "#808080"
    accent = None
    if item.accent:
        accent = _color(m.accent_color or item.default_accent or color, colors, "", sink) or color
    return ResolvedProp(name=name, style_profile=style, catalog=CATALOG_VERSION, item=item.ref, key=item.key,
                        category=item.category, params=full, size=item.size(full), finish=finish, color=color,
                        accent_color=accent, roughness=f.roughness, specular=f.specular, solid=item.solid,
                        rests_on=item.rests_on, surfaces=item.surfaces, seed=seed)


def _style(ref: str) -> tuple[str, dict[str, str]]:
    profile = get_style_profile(ref)
    return profile.ref, get_conventions(profile.ref).colors


def validate_prop(spec: PropSpecification) -> list[Issue]:
    try:
        _, colors = _style(spec.style_profile.ref)
    except KeyError as exc:
        return [Issue(path="style_profile", code="unknown_style_profile", message=str(exc.args[0]))]
    try:
        item = get_prop_def(spec.item.ref)
    except KeyError as exc:
        return [Issue(path="item", code="unknown_item", message=str(exc.args[0]))]
    issues: list[Issue] = []
    _prop_issues(item, spec.params, spec.material, colors, "", issues)
    return issues


def resolve_prop(spec: PropSpecification) -> ResolvedProp:
    issues = validate_prop(spec)
    if any(i.severity == "error" for i in issues):
        raise InvalidSpecification(issues)
    style, colors = _style(spec.style_profile.ref)
    return _resolve_prop(get_prop_def(spec.item.ref), spec.name, style, spec.params, spec.material, colors, spec.seed)


def _openings(t: EnvironmentDef, spec: EnvironmentSpecification) -> list[Opening]:
    if spec.openings is not None:
        return spec.openings
    if t.kind != "room":
        return []
    out = [Opening(kind="door", wall="north", offset=t.door_offset)] if t.door else []
    walls: tuple[Wall, ...] = ("east", "west", "north")
    for i in range(t.windows):
        out.append(Opening(kind="window", wall=walls[i % 3], offset=0.0 if i < 2 else -0.25))
    return out


def _dressing(t: EnvironmentDef, spec: EnvironmentSpecification, size: tuple[float, float, float]) -> list[Dressing]:
    if spec.dressing is not None:
        return spec.dressing
    out, n = [], {}
    for pl in t.dressing:
        item = get_prop_def(pl.prop)
        n[item.key] = n.get(item.key, 0) + 1
        out.append(Dressing(id=f"{item.key}_{n[item.key]}", prop=Ref(id=item.id, version=item.version),
                            at=(round(pl.at[0] * size[0], 3), round(pl.at[1] * size[1], 3)),
                            rotation_deg=pl.rotation_deg, params=dict(pl.params)))
    return out


def _resolve_openings(t: EnvironmentDef, ops: list[Opening], size) -> list[ResolvedOpening]:
    out = []
    for o in ops:
        length = size[0] if o.wall in ("north", "south") else size[1]
        if o.kind == "door":
            w, sill, head = o.width_m or 0.9, 0.0, min(2.05, size[2] - 0.2)
        else:
            w, sill, head = o.width_m or 1.2, 0.85, min(2.1, size[2] - 0.3)
        out.append(ResolvedOpening(kind=o.kind, wall=o.wall, offset_m=round(o.offset * length, 4), width_m=w,
                                   sill_m=sill, head_m=round(head, 4)))
    return out


def _door_inside(op: ResolvedOpening, size) -> tuple[float, float]:
    """The floor point just inside a door, where a character comes in."""
    w, d = size[0] / 2, size[1] / 2
    step = 0.45
    return {"north": (op.offset_m, d - step), "south": (op.offset_m, -d + step),
            "east": (w - step, op.offset_m), "west": (-w + step, op.offset_m)}[op.wall]


def validate_environment(spec: EnvironmentSpecification) -> list[Issue]:
    """Every check at once: template, size, materials, lighting, openings on their walls,
    dressing items (catalog, setting, inside the walls, not overlapping, ``on`` a top),
    marks on free floor, a door that can be walked through, cameras inside a room."""
    issues: list[Issue] = []
    try:
        _, colors = _style(spec.style_profile.ref)
    except KeyError as exc:
        return [Issue(path="style_profile", code="unknown_style_profile", message=str(exc.args[0]))]
    try:
        t = get_environment_def(spec.template.ref)
    except KeyError as exc:
        return [Issue(path="template", code="unknown_template", message=str(exc.args[0]))]
    size = spec.size or t.default_size
    axes = ("width", "depth", "height") if t.kind == "room" else ("width", "depth")
    for i, axis in enumerate(axes):
        if not t.min_size[i] <= size[i] <= t.max_size[i]:
            issues.append(Issue(path=f"size[{i}]", code="out_of_range",
                                message=f"a {t.label} {axis} is {t.min_size[i]}..{t.max_size[i]} m"))
    m = spec.materials
    if m.floor_finish is not None and m.floor_finish not in t.floor_finishes:
        issues.append(Issue(path="materials.floor_finish", code="not_for_template",
                            message=f"a {t.label} floor is {list(t.floor_finishes)}"))
    if m.wall_finish is not None and m.wall_finish not in t.wall_finishes:
        issues.append(Issue(path="materials.wall_finish", code="not_for_template",
                            message=f"a {t.label} has walls of {list(t.wall_finishes) or 'nothing (outdoor)'}"))
    for k in ("floor_color", "wall_color"):
        if getattr(m, k) is not None:
            _color(getattr(m, k), colors, f"materials.{k}", issues)
    if spec.lighting is not None and spec.lighting not in LIGHTING:
        issues.append(Issue(path="lighting", code="unknown_lighting", message=f"lighting is one of {sorted(LIGHTING)}"))
    if issues and any(i.path.startswith("size") for i in issues):
        return issues

    ops = _openings(t, spec)
    if t.kind != "room" and ops:
        issues.append(Issue(path="openings", code="not_for_template", message="an outdoor set has no walls"))
    rops = _resolve_openings(t, ops, size) if t.kind == "room" else []
    by_wall: dict[str, list[tuple[int, ResolvedOpening]]] = {}
    for i, o in enumerate(rops):
        length = size[0] if o.wall in ("north", "south") else size[1]
        if abs(o.offset_m) + o.width_m / 2 > length / 2 - 0.15:
            issues.append(Issue(path=f"openings[{i}]", code="off_wall",
                                message=f"a {o.width_m:.2f} m {o.kind} at {o.offset_m:+.2f} m does not fit the "
                                        f"{length:.2f} m {o.wall} wall"))
        for j, other in by_wall.get(o.wall, []):
            if abs(o.offset_m - other.offset_m) < (o.width_m + other.width_m) / 2 + 0.1:
                issues.append(Issue(path=f"openings[{i}]", code="overlap", message=f"it overlaps openings[{j}]"))
        by_wall.setdefault(o.wall, []).append((i, o))
        if o.kind == "door" and o.wall == "south":
            issues.append(Issue(path=f"openings[{i}].wall", code="cutaway_wall", severity="warning",
                                message="the south wall is the cutaway the cameras look through"))

    items = _dressing(t, spec, size)
    ids: dict[str, int] = {}
    rects: list[tuple[int, str, Rect, PropDef]] = []
    setting = "indoor" if t.kind == "room" else "outdoor"
    for i, dr in enumerate(items):
        p = f"dressing[{i}]"
        try:
            item = get_prop_def(dr.prop.ref)
        except KeyError as exc:
            issues.append(Issue(path=f"{p}.prop", code="unknown_item", message=str(exc.args[0])))
            continue
        did = dr.id or f"{item.key}_{i + 1}"
        if did in ids:
            issues.append(Issue(path=f"{p}.id", code="duplicate", message=f"dressing[{ids[did]}] is already {did!r}"))
        ids.setdefault(did, i)
        _prop_issues(item, dr.params, dr.material, colors, f"{p}.", issues)
        if item.setting not in ("any", setting):
            issues.append(Issue(path=f"{p}.prop", code="wrong_setting",
                                message=f"a {item.label} goes {item.setting}, this is {setting}"))
        sz = item.size({k: v for k, v in dr.params.items() if k in item.params})
        rect: Rect = (dr.at[0], dr.at[1], sz[0], sz[1], dr.rotation_deg)
        if dr.on is None:
            if item.rests_on == "top":
                issues.append(Issue(path=f"{p}.on", code="needs_surface",
                                    message=f"a {item.label} goes on top of something; name it with 'on'"))
            if not inside_bounds(rect, size[0], size[1], CLEARANCE_M):
                issues.append(Issue(path=f"{p}.at", code="outside", message=f"the {item.label} is not inside the "
                                                                            f"{size[0]} x {size[1]} m floor"))
            rects.append((i, did, rect, item))
    for a in range(len(rects)):
        for b in range(a + 1, len(rects)):
            (_ia, na, ra, pa), (ib, nb, rb, pb) = rects[a], rects[b]
            if (pa.solid or pb.solid) and overlap(ra, rb, CLEARANCE_M):
                issues.append(Issue(path=f"dressing[{ib}].at", code="overlap",
                                    message=f"{nb} overlaps {na}"))
    tops = {did: (rect, item) for _, did, rect, item in rects if any(s.kind == "top" for s in item.surfaces)}
    for i, dr in enumerate(items):
        if dr.on is None:
            continue
        base = tops.get(dr.on)
        if base is None:
            issues.append(Issue(path=f"dressing[{i}].on", code="no_surface",
                                message=f"no dressing item {dr.on!r} with a top to stand on"))
        elif not contains(base[0], dr.at[0], dr.at[1]):
            issues.append(Issue(path=f"dressing[{i}].at", code="off_surface", message=f"it is not on {dr.on}'s top"))

    # the resolved set answers "is this floor free" the same way the scene will ask it
    if not any(i.severity == "error" for i in issues):
        env = _resolve(spec, t, size, rops, items, colors, check=False)
        # the default ``centre`` mark is checked too (its path names the template's floor)
        for i, mk in enumerate(spec.marks or [Mark(name="centre", at=(0.0, 0.0))]):
            if mk.kind == "seat":
                seat = next((d for d in env.dressing if d.id == mk.item), None)
                if seat is None or not any(s.kind == "seat" for s in seat.prop.surfaces):
                    issues.append(Issue(path=f"marks[{i}].item", code="no_seat",
                                        message=f"no dressing item {mk.item!r} with a seat"))
            elif not env.walkable(*mk.at):
                issues.append(Issue(path=f"marks[{i}].at" if spec.marks else "marks", code="blocked",
                                    message=f"a character cannot stand at {mk.at}: a wall or a solid item is there"))
        for i, o in enumerate(env.openings):
            if o.kind == "door" and not env.walkable(*_door_inside(o, size)):
                issues.append(Issue(path=f"openings[{i}]", code="door_blocked",
                                    message="something solid stands just inside the door"))
        if t.kind == "room":
            for i, c in enumerate(spec.cameras):
                if abs(c.at[0]) > size[0] / 2 or c.at[1] > size[1] / 2 or not 0 < c.at[2] < size[2]:
                    issues.append(Issue(path=f"cameras[{i}].at", code="outside", severity="warning",
                                        message="the camera is outside the room; walls other than the cutaway "
                                                "will block it"))
    names = [mk.name for mk in spec.marks] + [c.name for c in spec.cameras]
    for n in {n for n in names if names.count(n) > 1}:
        issues.append(Issue(path="marks", code="duplicate", message=f"the name {n!r} is used twice"))
    return issues


def _default_cameras(t: EnvironmentDef, size) -> list[CameraMark]:
    w, d, h = size
    if t.kind == "room":
        eye = min(1.45, h - 0.4)
        return [CameraMark(name="wide", at=(0.0, -d / 2 - 0.2 * d, eye + 0.25), look_at=(0.0, 0.05 * d, 0.9),
                           lens_mm=24),
                CameraMark(name="front", at=(0.0, -d / 2 + 0.35, 1.1), look_at=(0.0, 0.0, 0.85), lens_mm=35)]
    return [CameraMark(name="wide", at=(0.0, -d / 2 - 1.5, 2.2), look_at=(0.0, 0.1 * d, 1.0), lens_mm=28),
            CameraMark(name="front", at=(0.0, -0.3 * d, 1.2), look_at=(0.0, 0.0, 0.85), lens_mm=40)]


def _resolve(spec, t, size, rops, items, colors, check=True) -> ResolvedEnvironment:
    style = get_style_profile(spec.style_profile.ref).ref
    m = spec.materials
    floor = m.floor_finish or t.default_floor
    wall = (m.wall_finish or t.default_wall) if t.kind == "room" else None
    sink: list[Issue] = []
    resolved: list[ResolvedDressing] = []
    by_id: dict[str, ResolvedDressing] = {}
    for i, dr in enumerate(items):
        item = get_prop_def(dr.prop.ref)
        did = dr.id or f"{item.key}_{i + 1}"
        rp = _resolve_prop(item, f"{did}", style, dr.params, dr.material, colors, spec.seed + i)
        z = 0.0
        if dr.on is not None and dr.on in by_id:
            z = by_id[dr.on].prop.surface_z("top") or 0.0
        rd = ResolvedDressing(id=did, prop=rp, at=(float(dr.at[0]), float(dr.at[1])), z=round(z, 4),
                              rotation_deg=float(dr.rotation_deg), on=dr.on)
        resolved.append(rd)
        by_id[did] = rd
    marks = []
    for mk in spec.marks or [Mark(name="centre", at=(0.0, 0.0))]:
        z = 0.0
        if mk.kind == "seat" and mk.item in by_id:
            seat = by_id[mk.item]
            z = seat.prop.surface_z("seat") or 0.0
            at = seat.at
        else:
            at = mk.at
        marks.append(ResolvedMark(name=mk.name, kind=mk.kind, at=(float(at[0]), float(at[1]), round(z, 4)),
                                  facing_deg=float(mk.facing_deg), item=mk.item))
    for o in rops:
        if o.kind == "door" and not any(mk.name == "door" for mk in marks):
            x, y = _door_inside(o, size)
            marks.append(ResolvedMark(name="door", kind="spawn", at=(round(x, 4), round(y, 4), 0.0),
                                      facing_deg=0.0, item=None))
    return ResolvedEnvironment(
        name=spec.name, style_profile=style, catalog=CATALOG_VERSION, template=t.ref, key=t.key, kind=t.kind,
        size=tuple(float(v) for v in size), floor_finish=floor,
        floor_color=_color(m.floor_color or t.default_floor_color, colors, "", sink) or "#888888",
        floor_roughness=FINISHES[floor].roughness, wall_finish=wall,
        wall_color=(_color(m.wall_color or t.default_wall_color, colors, "", sink) if wall else None),
        wall_roughness=FINISHES[wall].roughness if wall else None,
        lighting=LIGHTING[spec.lighting or t.default_lighting], openings=tuple(rops), dressing=tuple(resolved),
        marks=tuple(marks), cameras=tuple(spec.cameras or _default_cameras(t, size)), seed=spec.seed)


def resolve_environment(spec: EnvironmentSpecification) -> ResolvedEnvironment:
    issues = validate_environment(spec)
    if any(i.severity == "error" for i in issues):
        raise InvalidSpecification(issues)
    _, colors = _style(spec.style_profile.ref)
    t = get_environment_def(spec.template.ref)
    size = spec.size or t.default_size
    rops = _resolve_openings(t, _openings(t, spec), size) if t.kind == "room" else []
    return _resolve(spec, t, size, rops, _dressing(t, spec, size), colors)
