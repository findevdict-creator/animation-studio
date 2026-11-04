"""Character specification (architecture 4.2) and its resolution against a style profile.

Two steps, both pure Python so the API, the AI job and Blender agree:

* ``validate_spec`` checks a specification against the vocabulary of its style profile and
  returns every problem at once (an AI repair loop needs the full list, not the first error).
* ``resolve_spec`` turns a valid specification into ``ResolvedCharacter``: concrete metres,
  hex colours, slider values and the template and rig profile to use. The Blender job only
  ever sees the resolved form, so it never has to interpret style words.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from studio_contracts.rig_profile import BUILTIN_RIG_PROFILES, RigProfile
from studio_contracts.style_profile import HEX, StyleProfile, get_style_profile

SCHEMA_VERSION = "character_spec/1"

#: The only template in Phase 2: a procedural stand-in for the artist-made base template
#: (decision D11, open question Q2). Version 0.x on purpose: it is not production quality.
STANDIN_TEMPLATE = ("TPL-HUMANOID-STANDIN", "0.1")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ref(Model):
    id: str = Field(min_length=1, max_length=64)
    version: str = Field(pattern=r"^\d+\.\d+$")

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


Slider = float  # -1..1, checked in validate_spec so the message names the slider


class Body(Model):
    age_appearance: str
    body_type: str = "average"
    height_m: float | None = None
    head_body_ratio: float | None = None
    proportions: dict[str, Slider] = Field(default_factory=dict)


class Face(Model):
    shape: str = "rounded"
    eye_shape: str = "large_rounded"
    eye_size: float | None = None
    eye_color: str = "brown"  # preset name or #RRGGBB
    nose: str = "small"
    mouth: str = "friendly"
    ears: str = "default"
    sliders: dict[str, Slider] = Field(default_factory=dict)


class Hair(Model):
    style: str = "short"
    color: str = "dark_brown"  # preset name or #RRGGBB
    asset: Ref | None = None  # library hair asset; Phase 3+


class Skin(Model):
    preset: str | None = "warm_medium"
    color: str | None = Field(default=None, pattern=HEX)


class Materials(Model):
    skin: Skin = Field(default_factory=Skin)


class CharacterSpecification(Model):
    schema_version: Literal["character_spec/1"] = SCHEMA_VERSION
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    style_profile: Ref = Field(default_factory=lambda: Ref(id="family_cartoon", version="1.0"))
    template: Ref | None = None
    rig_profile: Ref | None = None
    body: Body
    face: Face = Field(default_factory=Face)
    hair: Hair = Field(default_factory=Hair)
    materials: Materials = Field(default_factory=Materials)
    preview_outfit: Ref | None = None
    expressions: Literal["rig_profile_default"] = "rig_profile_default"
    reference_images: list[str] = Field(default_factory=list, max_length=8)
    #: Advanced template parameters. The stand-in template exposes none, so this must be empty.
    overrides: dict[str, float] = Field(default_factory=dict)
    #: Seeds the small procedural variations (hair strand jitter). Same spec, same mesh.
    seed: int = Field(default=0, ge=0, lt=2**31)


class Issue(Model):
    path: str
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"


class ResolvedCharacter(Model):
    """Everything the Blender build needs, in numbers. Hash-stable for caching and audit."""

    name: str
    style_profile: str
    template: str
    rig_profile: str
    fit_profile: str
    age_appearance: str
    body_type: str
    height_m: float
    head_body_ratio: float
    limb_thickness: float
    torso_width: float
    belly: float
    limb: float
    proportions: dict[str, float]
    face_shape: str
    eye_shape: str
    eye_size: float
    eye_color: str
    nose: str
    mouth: str
    ears: str
    face_sliders: dict[str, float]
    hair_style: str
    hair_color: str
    skin_color: str
    skin_roughness: float
    skin_specular: float
    skin_subsurface: float
    hair_roughness: float
    eye_roughness: float
    seed: int

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()


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


def _enum(value: str, allowed: tuple[str, ...] | list[str], path: str, issues: list[Issue]) -> None:
    if value not in allowed:
        issues.append(Issue(path=path, code="not_in_vocabulary",
                            message=f"{value!r} is not one of {list(allowed)}"))


def _sliders(values: dict[str, float], allowed: tuple[str, ...], path: str, issues: list[Issue]) -> None:
    for key, v in values.items():
        if key not in allowed:
            issues.append(Issue(path=f"{path}.{key}", code="unknown_slider",
                                message=f"no slider {key!r}; the template exposes {list(allowed)}"))
        elif not -1.0 <= v <= 1.0:
            issues.append(Issue(path=f"{path}.{key}", code="out_of_range", message=f"{v} is outside -1..1"))


def _range_warning(value: float | None, lo: float, hi: float, path: str, issues: list[Issue]) -> None:
    if value is not None and not lo <= value <= hi:
        issues.append(Issue(path=path, code="clamped", severity="warning",
                            message=f"{value} is outside {lo}..{hi} for this style and will be clamped"))


def validate_spec(spec: CharacterSpecification, profile: StyleProfile | None = None) -> list[Issue]:
    issues: list[Issue] = []
    if profile is None:
        try:
            profile = get_style_profile(spec.style_profile.ref)
        except KeyError as exc:
            return [Issue(path="style_profile", code="unknown_style_profile", message=str(exc))]
    rig_ref = spec.rig_profile.ref if spec.rig_profile else profile.rig_profile
    if rig_ref not in BUILTIN_RIG_PROFILES:
        issues.append(Issue(path="rig_profile", code="unknown_rig_profile", message=f"no rig profile {rig_ref!r}"))
    if spec.template and (spec.template.id, spec.template.version) != STANDIN_TEMPLATE:
        issues.append(Issue(path="template", code="unknown_template",
                            message=f"only {STANDIN_TEMPLATE[0]}@{STANDIN_TEMPLATE[1]} exists in this release"))

    b = spec.body
    age = profile.age_appearances.get(b.age_appearance)
    if age is None:
        _enum(b.age_appearance, list(profile.age_appearances), "body.age_appearance", issues)
    else:
        _enum(b.body_type, age.body_types, "body.body_type", issues)
        _range_warning(b.height_m, age.height_m.min, age.height_m.max, "body.height_m", issues)
        _range_warning(b.head_body_ratio, age.head_body_ratio.min, age.head_body_ratio.max,
                       "body.head_body_ratio", issues)
    _sliders(b.proportions, profile.body_sliders, "body.proportions", issues)

    f = spec.face
    _enum(f.shape, profile.face_shapes, "face.shape", issues)
    _enum(f.eye_shape, profile.eye_shapes, "face.eye_shape", issues)
    _enum(f.nose, profile.noses, "face.nose", issues)
    _enum(f.mouth, profile.mouths, "face.mouth", issues)
    _enum(f.ears, profile.ears, "face.ears", issues)
    _range_warning(f.eye_size, profile.eye_size.min, profile.eye_size.max, "face.eye_size", issues)
    _color(f.eye_color, profile.eye_colors, "face.eye_color", issues)
    _sliders(f.sliders, profile.face_sliders, "face.sliders", issues)

    _enum(spec.hair.style, profile.hair_styles, "hair.style", issues)
    _color(spec.hair.color, profile.hair_colors, "hair.color", issues)
    if spec.hair.asset is not None:
        issues.append(Issue(path="hair.asset", code="not_supported",
                            message="library hair assets arrive with the asset library (Phase 3); use hair.style"))

    skin = spec.materials.skin
    if skin.color is None:
        if skin.preset is None:
            issues.append(Issue(path="materials.skin", code="missing", message="give a preset or a color"))
        else:
            _color(skin.preset, profile.skin_presets, "materials.skin.preset", issues)
    if spec.overrides:
        issues.append(Issue(path="overrides", code="not_supported",
                            message="the stand-in template exposes no override parameters"))
    if spec.preview_outfit is not None:
        issues.append(Issue(path="preview_outfit", code="not_supported", severity="warning",
                            message="outfits arrive in Phase 4; the preview shows the base body"))
    return issues


def resolve_spec(spec: CharacterSpecification, profile: StyleProfile | None = None) -> ResolvedCharacter:
    profile = profile or get_style_profile(spec.style_profile.ref)
    issues = validate_spec(spec, profile)
    if any(i.severity == "error" for i in issues):
        raise InvalidSpecification(issues)
    age = profile.age_appearances[spec.body.age_appearance]
    bt = profile.body_types[spec.body.body_type]
    skin = spec.materials.skin
    skin_color = skin.color.upper() if skin.color else profile.skin_presets[skin.preset]  # type: ignore[index]
    sink: list[Issue] = []
    return ResolvedCharacter(
        name=spec.name,
        style_profile=profile.ref,
        template=f"{STANDIN_TEMPLATE[0]}@{STANDIN_TEMPLATE[1]}",
        rig_profile=spec.rig_profile.ref if spec.rig_profile else profile.rig_profile,
        fit_profile=profile.fit_profile(spec.body.age_appearance, spec.body.body_type),
        age_appearance=spec.body.age_appearance,
        body_type=spec.body.body_type,
        height_m=round(age.height_m.clamp(spec.body.height_m), 4),
        head_body_ratio=round(age.head_body_ratio.clamp(spec.body.head_body_ratio), 4),
        limb_thickness=age.limb_thickness,
        torso_width=bt.torso_width, belly=bt.belly, limb=bt.limb,
        proportions={k: spec.body.proportions.get(k, 0.0) for k in profile.body_sliders},
        face_shape=spec.face.shape, eye_shape=spec.face.eye_shape,
        eye_size=profile.eye_size.clamp(spec.face.eye_size),
        eye_color=_color(spec.face.eye_color, profile.eye_colors, "", sink) or "#000000",
        nose=spec.face.nose, mouth=spec.face.mouth, ears=spec.face.ears,
        face_sliders={k: spec.face.sliders.get(k, 0.0) for k in profile.face_sliders},
        hair_style=spec.hair.style,
        hair_color=_color(spec.hair.color, profile.hair_colors, "", sink) or "#000000",
        skin_color=skin_color,
        skin_roughness=profile.skin.roughness, skin_specular=profile.skin.specular,
        skin_subsurface=profile.skin.subsurface,
        hair_roughness=profile.hair.roughness, eye_roughness=profile.eyes.roughness,
        seed=spec.seed,
    )


def get_rig_profile(ref: str) -> RigProfile:
    return BUILTIN_RIG_PROFILES[ref]


def llm_json_schema(profile: StyleProfile) -> dict:
    """JSON schema sent to the LLM: the specification with every vocabulary closed as an enum
    from ``profile``, so a model cannot even propose a value the template lacks (SRS 5.2)."""
    schema = CharacterSpecification.model_json_schema()
    defs = schema.get("$defs", {})

    def enum(model: str, field: str, values) -> None:
        defs[model]["properties"][field] = {"type": "string", "enum": list(values)}

    enum("Body", "age_appearance", profile.age_appearances)
    enum("Body", "body_type", profile.body_types)
    enum("Face", "shape", profile.face_shapes)
    enum("Face", "eye_shape", profile.eye_shapes)
    enum("Face", "nose", profile.noses)
    enum("Face", "mouth", profile.mouths)
    enum("Face", "ears", profile.ears)
    enum("Face", "eye_color", profile.eye_colors)
    enum("Hair", "style", profile.hair_styles)
    enum("Hair", "color", profile.hair_colors)
    defs["Skin"]["properties"]["preset"] = {"type": "string", "enum": list(profile.skin_presets)}
    for model, field, names in (("Body", "proportions", profile.body_sliders), ("Face", "sliders", profile.face_sliders)):
        defs[model]["properties"][field] = {
            "type": "object", "additionalProperties": False,
            "properties": {n: {"type": "number", "minimum": -1, "maximum": 1} for n in names}}
    # Fields the AI must not set: code picks them.
    for f in ("template", "rig_profile", "preview_outfit", "overrides", "reference_images", "seed", "schema_version",
              "style_profile"):
        schema["properties"].pop(f, None)
    defs["Skin"]["properties"].pop("color", None)
    defs["Hair"]["properties"].pop("asset", None)
    schema["required"] = ["name", "body", "face", "hair", "materials"]
    return schema
