"""Outfit specification (SRS 7, architecture 9) and its resolution against the garment
catalog and the clothing conventions of a style profile.

An outfit is its own asset (``OUTFIT-001``) with its own versions. It is a list of pieces,
one per slot (any number of accessories), each naming a catalog garment, its parameters and
its material. Like characters, the check is pure Python so the API, the AI job and Blender
agree:

* ``validate_outfit`` returns every problem at once;
* ``resolve_outfit`` turns a valid specification into ``ResolvedOutfit``: concrete numbers,
  hex colours, fabric shading values, the bones rigid pieces attach to and the body regions
  each piece covers and hides. Blender only ever sees the resolved form.

The garment catalog is a **procedural stand-in** (``GARM-*@0.1``), in the same way the
character template is (decision D11, open question Q2): every garment is generated from the
reference body of a body-fit profile, not modelled by an artist. Version 0.x on purpose.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from studio_contracts.character import CharacterSpecification, Issue, Ref
from studio_contracts.ids import CODE_RE
from studio_contracts.rig_profile import BUILTIN_RIG_PROFILES
from studio_contracts.style_profile import HEX, StyleProfile, get_style_profile

SCHEMA_VERSION = "outfit_spec/1"
CATALOG_VERSION = "0.1"

Slot = Literal["upper", "lower", "footwear", "headwear", "gloves", "accessory"]
SLOTS: tuple[str, ...] = ("upper", "lower", "footwear", "headwear", "gloves", "accessory")
#: how a piece follows the body: ``skinned`` garments take weights from the body they are
#: fitted to; ``rigid`` pieces are bound with weight 1 to the bone of an attachment point
Binding = Literal["skinned", "rigid"]
Scope = Literal["character", "body_profile", "multi"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ catalog
class ParamDef(_Frozen):
    min: float
    max: float
    default: float
    description: str


class GarmentDef(_Frozen):
    id: str = Field(pattern=r"^GARM-[A-Z0-9]+$")
    version: str
    key: str
    slot: str
    label: str
    binding: Binding
    #: rig-profile attachment point a rigid piece is bound to
    attachment: str | None = None
    params: dict[str, ParamDef] = Field(default_factory=dict)
    fabrics: tuple[str, ...]
    default_fabric: str
    default_color: str
    trim: bool = Field(default=False, description="has cuffs/hems that can take a second colour")
    #: rig-profile mask regions the piece touches (compatibility, body masking)
    covers: tuple[str, ...]
    #: regions the piece hides completely at every parameter value
    hides: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


def _p(lo: float, hi: float, default: float, text: str) -> ParamDef:
    return ParamDef(min=lo, max=hi, default=default, description=text)


EASE = _p(0.0, 1.0, 0.35, "0 is close-fitting, 1 is loose")
_TOPS = ("cotton", "knit", "nylon")
GARMENTS: tuple[GarmentDef, ...] = (
    GarmentDef(id="GARM-TSHIRT", version=CATALOG_VERSION, key="tshirt", slot="upper", label="T-shirt",
               binding="skinned", fabrics=_TOPS, default_fabric="cotton", default_color="white", trim=True,
               params={"ease": EASE, "sleeve_length": _p(0.25, 0.9, 0.5, "share of the upper arm the sleeve covers"),
                       "hem": _p(0.0, 1.0, 0.5, "0 ends at the waist, 1 at the hip")},
               covers=("torso", "upper_arm", "neck"), hides=("torso",)),
    GarmentDef(id="GARM-LONGSLEEVE", version=CATALOG_VERSION, key="longsleeve", slot="upper",
               label="Long-sleeve top", binding="skinned", fabrics=_TOPS, default_fabric="cotton",
               default_color="navy", trim=True,
               params={"ease": EASE, "sleeve_length": _p(0.6, 1.0, 0.95, "share of the forearm the sleeve covers"),
                       "hem": _p(0.0, 1.0, 0.5, "0 ends at the waist, 1 at the hip")},
               covers=("torso", "upper_arm", "forearm", "neck"), hides=("torso", "upper_arm")),
    GarmentDef(id="GARM-SWEATER", version=CATALOG_VERSION, key="sweater", slot="upper", label="Sweater",
               binding="skinned", fabrics=("knit", "wool", "cotton"), default_fabric="knit",
               default_color="forest_green", trim=True,
               params={"ease": _p(0.2, 1.0, 0.6, "0 is close-fitting, 1 is loose"),
                       "sleeve_length": _p(0.6, 1.0, 0.95, "share of the forearm the sleeve covers"),
                       "hem": _p(0.0, 1.0, 0.8, "0 ends at the waist, 1 at the hip")},
               covers=("torso", "upper_arm", "forearm", "neck"), hides=("torso", "upper_arm")),
    GarmentDef(id="GARM-SHORTS", version=CATALOG_VERSION, key="shorts", slot="lower", label="Shorts",
               binding="skinned", fabrics=("cotton", "denim", "nylon"), default_fabric="cotton",
               default_color="khaki", trim=False,
               params={"ease": EASE, "length": _p(0.2, 0.9, 0.5, "share of the thigh the leg covers")},
               covers=("torso", "thigh")),
    GarmentDef(id="GARM-TROUSERS", version=CATALOG_VERSION, key="trousers", slot="lower", label="Trousers",
               binding="skinned", fabrics=("denim", "cotton", "wool"), default_fabric="denim",
               default_color="denim_blue", trim=True,
               params={"ease": EASE, "length": _p(0.6, 1.0, 0.92, "share of the shin the leg covers")},
               covers=("torso", "thigh", "shin"), hides=("thigh",)),
    GarmentDef(id="GARM-SNEAKERS", version=CATALOG_VERSION, key="sneakers", slot="footwear", label="Sneakers",
               binding="skinned", fabrics=("canvas", "leather", "nylon"), default_fabric="canvas",
               default_color="red", trim=True,
               params={"collar": _p(0.0, 0.3, 0.08, "share of the shin above the ankle the collar covers")},
               covers=("foot", "shin"), hides=("foot",)),
    GarmentDef(id="GARM-BOOTS", version=CATALOG_VERSION, key="boots", slot="footwear", label="Boots",
               binding="skinned", fabrics=("leather", "rubber"), default_fabric="leather",
               default_color="brown", trim=True,
               params={"collar": _p(0.3, 0.7, 0.45, "share of the shin above the ankle the boot covers")},
               covers=("foot", "shin"), hides=("foot",)),
    GarmentDef(id="GARM-CAP", version=CATALOG_VERSION, key="cap", slot="headwear", label="Baseball cap",
               binding="rigid", attachment="head_top", fabrics=("cotton", "canvas", "nylon"),
               default_fabric="cotton", default_color="red",
               params={"brim": _p(0.0, 1.0, 0.6, "brim length, 0 is short"),
                       "crown": _p(0.0, 1.0, 0.5, "how far down the head the crown sits")},
               covers=("head",)),
    GarmentDef(id="GARM-BACKPACK", version=CATALOG_VERSION, key="backpack", slot="accessory", label="Backpack",
               binding="rigid", attachment="back_center", fabrics=("nylon", "canvas", "leather"),
               default_fabric="nylon", default_color="orange", trim=True,
               params={"size": _p(0.0, 1.0, 0.5, "0 is small, 1 is large")},
               covers=("torso",)),
)
GARMENT_CATALOG: dict[str, GarmentDef] = {g.ref: g for g in GARMENTS}
GARMENT_BY_KEY: dict[str, GarmentDef] = {g.key: g for g in GARMENTS}


def get_garment(ref: str) -> GarmentDef:
    """``GARM-TSHIRT@0.1``, or a catalog key such as ``tshirt``."""
    if ref in GARMENT_CATALOG:
        return GARMENT_CATALOG[ref]
    if ref in GARMENT_BY_KEY:
        return GARMENT_BY_KEY[ref]
    raise KeyError(f"no garment {ref!r} in catalog {CATALOG_VERSION}")


# ------------------------------------------------------------------ conventions
class Fabric(_Frozen):
    roughness: float = Field(ge=0, le=1)
    specular: float = Field(ge=0, le=1)
    sheen: float = Field(ge=0, le=1, default=0.0)
    #: shell thickness in metres (Solidify)
    thickness: float = Field(gt=0, lt=0.02)


class FitThresholds(_Frozen):
    """What the fit check accepts (architecture 9.3). A garment fails a pose when more than
    ``max_clip_share`` of the body points it covers poke through, or any by more than
    ``max_clip_depth_m``."""

    max_clip_share: float = Field(ge=0, le=1)
    max_clip_depth_m: float = Field(ge=0)
    max_edge_stretch: float = Field(gt=1)
    min_edge_stretch: float = Field(gt=0, lt=1)
    #: how far a skinned garment may move away from the body surface in a pose
    max_lift_m: float = Field(gt=0)


class ClothingConventions(_Frozen):
    """Clothing rules for one style profile. Kept beside, not inside, the style profile so
    the published ``family_cartoon@1.0`` is not changed."""

    style_profile: str
    colors: dict[str, str]
    fabrics: dict[str, Fabric]
    #: gap between the body and a garment's inner surface, in metres, at ease 0 and ease 1
    clearance_m: tuple[float, float]
    #: nothing is placed closer to the body than this after fitting
    min_clearance_m: float
    #: body vertices further than this inside a garment's coverage are hidden by the mask
    mask_margin_m: float
    fit: FitThresholds
    poses: tuple[str, ...]


FAMILY_CARTOON_CLOTHING = ClothingConventions(
    style_profile="family_cartoon@1.0",
    colors={
        "white": "#F2F0EA", "black": "#1E1E22", "grey": "#8A8D92", "navy": "#24324F", "royal_blue": "#2F5BB7",
        "sky_blue": "#7FB2E5", "denim_blue": "#3B5878", "red": "#C8322B", "orange": "#E07A28",
        "yellow": "#F2C230", "forest_green": "#2F5D3A", "lime": "#8CC63F", "purple": "#6A4A9A",
        "pink": "#E58BAA", "brown": "#6B4428", "khaki": "#B8A27A", "cream": "#EADFC8", "teal": "#2A8C8C",
    },
    fabrics={
        "cotton": Fabric(roughness=0.85, specular=0.15, sheen=0.2, thickness=0.004),
        "knit": Fabric(roughness=0.95, specular=0.08, sheen=0.4, thickness=0.006),
        "wool": Fabric(roughness=0.95, specular=0.06, sheen=0.5, thickness=0.006),
        "denim": Fabric(roughness=0.8, specular=0.12, sheen=0.1, thickness=0.005),
        "nylon": Fabric(roughness=0.45, specular=0.35, sheen=0.0, thickness=0.003),
        "canvas": Fabric(roughness=0.8, specular=0.15, thickness=0.004),
        "leather": Fabric(roughness=0.5, specular=0.35, thickness=0.005),
        "rubber": Fabric(roughness=0.6, specular=0.2, thickness=0.006),
    },
    clearance_m=(0.004, 0.022),
    min_clearance_m=0.002,
    mask_margin_m=0.025,
    fit=FitThresholds(max_clip_share=0.005, max_clip_depth_m=0.004, max_edge_stretch=1.6,
                      min_edge_stretch=0.3, max_lift_m=0.05),
    poses=("rest", "t_pose", "arms_up", "arms_forward", "walk", "sit", "crouch", "twist", "head_turn"),
)
CLOTHING_CONVENTIONS: dict[str, ClothingConventions] = {FAMILY_CARTOON_CLOTHING.style_profile: FAMILY_CARTOON_CLOTHING}


def get_conventions(style_ref: str) -> ClothingConventions:
    profile = get_style_profile(style_ref)
    try:
        return CLOTHING_CONVENTIONS[profile.ref]
    except KeyError:
        raise KeyError(f"style profile {profile.ref} has no clothing conventions") from None


def fit_profiles(profile: StyleProfile) -> list[str]:
    """Every body-fit profile a style defines, e.g. ``child_slim`` .. ``elder_stocky``."""
    return [profile.fit_profile(age, bt) for age, a in profile.age_appearances.items() for bt in a.body_types]


def reference_character(fit_profile: str, style_ref: str = "family_cartoon@1.0") -> CharacterSpecification:
    """The neutral body garments of ``fit_profile`` are generated on: the style's default
    height and head ratio for the age, no sliders. Fitting maps from it to a real character."""
    profile = get_style_profile(style_ref)
    if fit_profile not in fit_profiles(profile):
        raise KeyError(f"style {profile.ref} has no fit profile {fit_profile!r}")
    age, body_type = fit_profile.rsplit("_", 1)
    return CharacterSpecification(name=f"REF_{fit_profile}", style_profile=Ref(id=profile.id, version=profile.version),
                                  body={"age_appearance": age, "body_type": body_type}, hair={"style": "bald"})


# ------------------------------------------------------------------ specification
class PieceMaterial(Model):
    fabric: str | None = None  # garment default when omitted
    color: str | None = None  # preset name or #RRGGBB; garment default when omitted
    trim_color: str | None = None


class Piece(Model):
    slot: Slot
    garment: Ref
    params: dict[str, float] = Field(default_factory=dict)
    material: PieceMaterial = Field(default_factory=PieceMaterial)


class OutfitSpecification(Model):
    schema_version: Literal["outfit_spec/1"] = SCHEMA_VERSION
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    style_profile: Ref = Field(default_factory=lambda: Ref(id="family_cartoon", version="1.0"))
    rig_profile: Ref | None = None
    #: ``character``: made for one character; ``body_profile``: any character of one fit
    #: profile; ``multi``: several fit profiles (each verified separately)
    scope: Scope = "body_profile"
    owner_character: str | None = Field(default=None, description="display code, for scope 'character'")
    #: the first is the one the geometry is generated on
    fit_profiles: list[str] = Field(min_length=1, max_length=8)
    pieces: list[Piece] = Field(min_length=1, max_length=8)
    cloth_simulation: bool = False
    reference_images: list[str] = Field(default_factory=list, max_length=8)
    seed: int = Field(default=0, ge=0, lt=2**31)


class ResolvedPiece(_Frozen):
    slot: str
    garment: str  # GARM-X@0.1
    key: str
    binding: Binding
    attachment: str | None
    attach_bone: str | None
    params: dict[str, float]
    fabric: str
    color: str
    trim_color: str | None
    roughness: float
    specular: float
    sheen: float
    thickness: float
    clearance: float
    covers: tuple[str, ...]
    hides: tuple[str, ...]

    @property
    def object_name(self) -> str:
        return f"{self.slot}_{self.key}"


class ResolvedOutfit(_Frozen):
    name: str
    style_profile: str
    rig_profile: str
    catalog: str
    primary_fit_profile: str
    fit_profiles: tuple[str, ...]
    scope: str
    owner_character: str | None
    pieces: tuple[ResolvedPiece, ...]
    min_clearance: float
    mask_margin: float
    seed: int

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()

    @property
    def covers(self) -> list[str]:
        return sorted({r for p in self.pieces for r in p.covers})

    @property
    def hides(self) -> list[str]:
        return sorted({r for p in self.pieces for r in p.hides})


class InvalidOutfit(ValueError):
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


def validate_outfit(spec: OutfitSpecification) -> list[Issue]:
    issues: list[Issue] = []
    try:
        profile = get_style_profile(spec.style_profile.ref)
        conv = get_conventions(profile.ref)
    except KeyError as exc:
        return [Issue(path="style_profile", code="unknown_style_profile", message=str(exc.args[0]))]
    rig_ref = spec.rig_profile.ref if spec.rig_profile else profile.rig_profile
    rig = BUILTIN_RIG_PROFILES.get(rig_ref)
    if rig is None:
        issues.append(Issue(path="rig_profile", code="unknown_rig_profile", message=f"no rig profile {rig_ref!r}"))

    known = fit_profiles(profile)
    for i, fp in enumerate(spec.fit_profiles):
        if fp not in known:
            issues.append(Issue(path=f"fit_profiles[{i}]", code="unknown_fit_profile",
                                message=f"{fp!r} is not one of {known}"))
    if len(set(spec.fit_profiles)) != len(spec.fit_profiles):
        issues.append(Issue(path="fit_profiles", code="duplicate", message="a fit profile is listed twice"))
    if spec.scope == "character":
        if spec.owner_character is None or not CODE_RE.match(spec.owner_character) \
                or not spec.owner_character.startswith("CHAR-"):
            issues.append(Issue(path="owner_character", code="missing",
                                message="a character-specific outfit names its character (CHAR-NNN)"))
        if len(spec.fit_profiles) != 1:
            issues.append(Issue(path="fit_profiles", code="scope_mismatch",
                                message="a character-specific outfit has exactly the character's fit profile"))
    else:
        if spec.owner_character is not None:
            issues.append(Issue(path="owner_character", code="scope_mismatch",
                                message=f"only scope 'character' names an owner (scope is {spec.scope!r})"))
        if spec.scope == "body_profile" and len(spec.fit_profiles) != 1:
            issues.append(Issue(path="fit_profiles", code="scope_mismatch",
                                message="scope 'body_profile' has exactly one fit profile; use 'multi' for more"))
        if spec.scope == "multi" and len(spec.fit_profiles) < 2:
            issues.append(Issue(path="fit_profiles", code="scope_mismatch",
                                message="scope 'multi' lists at least two fit profiles"))

    seen: dict[str, int] = {}
    for i, piece in enumerate(spec.pieces):
        path = f"pieces[{i}]"
        if piece.slot != "accessory" and piece.slot in seen:
            issues.append(Issue(path=f"{path}.slot", code="duplicate_slot",
                                message=f"pieces[{seen[piece.slot]}] already fills {piece.slot!r}"))
        seen.setdefault(piece.slot, i)
        try:
            g = get_garment(piece.garment.ref)
        except KeyError as exc:
            issues.append(Issue(path=f"{path}.garment", code="unknown_garment", message=str(exc.args[0])))
            continue
        if g.slot != piece.slot:
            issues.append(Issue(path=f"{path}.slot", code="wrong_slot",
                                message=f"{g.ref} ({g.label}) goes in {g.slot!r}, not {piece.slot!r}"))
        for name, value in piece.params.items():
            d = g.params.get(name)
            if d is None:
                issues.append(Issue(path=f"{path}.params.{name}", code="unknown_param",
                                    message=f"{g.label} has parameters {sorted(g.params)}"))
            elif not d.min <= value <= d.max:
                issues.append(Issue(path=f"{path}.params.{name}", code="out_of_range",
                                    message=f"{value} is outside {d.min}..{d.max}"))
        m = piece.material
        if m.fabric is not None:
            if m.fabric not in conv.fabrics:
                issues.append(Issue(path=f"{path}.material.fabric", code="not_in_vocabulary",
                                    message=f"{m.fabric!r} is not one of {sorted(conv.fabrics)}"))
            elif m.fabric not in g.fabrics:
                issues.append(Issue(path=f"{path}.material.fabric", code="not_for_garment",
                                    message=f"a {g.label} is made of {list(g.fabrics)}"))
        if m.color is not None:
            _color(m.color, conv.colors, f"{path}.material.color", issues)
        if m.trim_color is not None:
            if not g.trim:
                issues.append(Issue(path=f"{path}.material.trim_color", code="not_supported", severity="warning",
                                    message=f"a {g.label} has no trim; the colour is ignored"))
            else:
                _color(m.trim_color, conv.colors, f"{path}.material.trim_color", issues)
        if g.attachment and rig is not None and g.attachment not in rig.attachment_points:
            issues.append(Issue(path=f"{path}.garment", code="no_attachment_point",
                                message=f"rig profile {rig.ref} has no attachment point {g.attachment!r}"))
    if spec.cloth_simulation:
        issues.append(Issue(path="cloth_simulation", code="not_supported", severity="warning",
                            message="cloth simulation is not part of this release; garments are skinned only"))
    return issues


def resolve_outfit(spec: OutfitSpecification) -> ResolvedOutfit:
    issues = validate_outfit(spec)
    if any(i.severity == "error" for i in issues):
        raise InvalidOutfit(issues)
    profile = get_style_profile(spec.style_profile.ref)
    conv = get_conventions(profile.ref)
    rig = BUILTIN_RIG_PROFILES[spec.rig_profile.ref if spec.rig_profile else profile.rig_profile]
    lo, hi = conv.clearance_m
    pieces = []
    for piece in spec.pieces:
        g = get_garment(piece.garment.ref)
        params = {n: float(piece.params.get(n, d.default)) for n, d in g.params.items()}
        fabric_name = piece.material.fabric or g.default_fabric
        fabric = conv.fabrics[fabric_name]
        sink: list[Issue] = []
        color = _color(piece.material.color or g.default_color, conv.colors, "", sink)
        trim = None
        if g.trim:
            trim = _color(piece.material.trim_color, conv.colors, "", sink) if piece.material.trim_color else color
        ease = params.get("ease", 0.0)
        pieces.append(ResolvedPiece(
            slot=piece.slot, garment=g.ref, key=g.key, binding=g.binding, attachment=g.attachment,
            attach_bone=rig.attachment_points[g.attachment] if g.attachment else None,
            params=params, fabric=fabric_name, color=color or "#808080", trim_color=trim,
            roughness=fabric.roughness, specular=fabric.specular, sheen=fabric.sheen, thickness=fabric.thickness,
            clearance=round(lo + (hi - lo) * ease, 5), covers=g.covers, hides=g.hides))
    return ResolvedOutfit(
        name=spec.name, style_profile=profile.ref, rig_profile=rig.ref, catalog=CATALOG_VERSION,
        primary_fit_profile=spec.fit_profiles[0], fit_profiles=tuple(spec.fit_profiles), scope=spec.scope,
        owner_character=spec.owner_character, pieces=tuple(pieces), min_clearance=conv.min_clearance_m,
        mask_margin=conv.mask_margin_m, seed=spec.seed)

