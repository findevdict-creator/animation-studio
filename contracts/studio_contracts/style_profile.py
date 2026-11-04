"""Style profiles (SRS 9): one place that says what a series' characters may look like.

A profile is versioned and immutable once published. The character specification may only
use the vocabulary a profile defines, and ``resolve`` turns a specification into concrete
numbers once, in code, so no AI job and no Blender job ever interprets a style on its own.

Phase 2 ships one built-in profile, ``family_cartoon@1.0``. Phase 3 moves profiles into the
versioned asset library; this module stays the schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

HEX = r"^#[0-9A-Fa-f]{6}$"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Range(Model):
    min: float
    max: float
    default: float

    @model_validator(mode="after")
    def _ordered(self) -> "Range":
        if not self.min <= self.default <= self.max:
            raise ValueError(f"default {self.default} outside {self.min}..{self.max}")
        return self

    def clamp(self, value: float | None) -> float:
        return self.default if value is None else max(self.min, min(self.max, value))


class AgeProfile(Model):
    height_m: Range
    head_body_ratio: Range = Field(description="head height / total height")
    #: limb thickness relative to height; children are rounder
    limb_thickness: float = Field(gt=0, lt=0.2)
    body_types: tuple[str, ...]


class BodyType(Model):
    """Multipliers on the age profile's base girths."""

    torso_width: float = Field(gt=0.5, lt=2)
    belly: float = Field(gt=0.5, lt=2)
    limb: float = Field(gt=0.5, lt=2)


class ToonMaterial(Model):
    roughness: float = Field(ge=0, le=1)
    specular: float = Field(ge=0, le=1)
    subsurface: float = Field(ge=0, le=0.3, default=0.0)


class RenderSettings(Model):
    engine: Literal["CYCLES", "BLENDER_EEVEE"] = "CYCLES"
    preview_samples: int = Field(ge=1, le=4096)
    preview_resolution: int = Field(ge=64, le=4096)
    focal_length_mm: float = Field(gt=10, lt=300)
    fps: int = 24


class StyleProfile(Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=r"^\d+\.\d+$")
    description: str = ""
    age_appearances: dict[str, AgeProfile]
    body_types: dict[str, BodyType]
    face_shapes: tuple[str, ...]
    eye_shapes: tuple[str, ...]
    noses: tuple[str, ...]
    mouths: tuple[str, ...]
    ears: tuple[str, ...]
    hair_styles: tuple[str, ...]
    skin_presets: dict[str, str]  # name -> hex
    hair_colors: dict[str, str]
    eye_colors: dict[str, str]
    body_sliders: tuple[str, ...]
    face_sliders: tuple[str, ...]
    eye_size: Range
    skin: ToonMaterial
    hair: ToonMaterial
    eyes: ToonMaterial
    render: RenderSettings
    rig_profile: str = "cartoon_humanoid@1.0"

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def fit_profile(self, age: str, body_type: str) -> str:
        """``child_average`` etc. (architecture 9.1)."""
        return f"{age}_{body_type}"


FAMILY_CARTOON_V1 = StyleProfile(
    id="family_cartoon",
    version="1.0",
    description="Soft, rounded family-animation look: big heads, large eyes, short limbs, matte toon materials.",
    age_appearances={
        "child": AgeProfile(height_m=Range(min=0.95, max=1.45, default=1.25),
                            head_body_ratio=Range(min=0.26, max=0.38, default=0.32),
                            limb_thickness=0.055, body_types=("slim", "average", "stocky")),
        "teen": AgeProfile(height_m=Range(min=1.40, max=1.80, default=1.60),
                           head_body_ratio=Range(min=0.20, max=0.28, default=0.24),
                           limb_thickness=0.045, body_types=("slim", "average", "stocky")),
        "adult": AgeProfile(height_m=Range(min=1.50, max=2.00, default=1.75),
                            head_body_ratio=Range(min=0.16, max=0.24, default=0.20),
                            limb_thickness=0.042, body_types=("slim", "average", "stocky")),
        "elder": AgeProfile(height_m=Range(min=1.45, max=1.85, default=1.65),
                            head_body_ratio=Range(min=0.17, max=0.24, default=0.20),
                            limb_thickness=0.042, body_types=("slim", "average", "stocky")),
    },
    body_types={
        "slim": BodyType(torso_width=0.85, belly=0.8, limb=0.85),
        "average": BodyType(torso_width=1.0, belly=1.0, limb=1.0),
        "stocky": BodyType(torso_width=1.2, belly=1.3, limb=1.2),
    },
    face_shapes=("rounded", "oval", "square", "heart"),
    eye_shapes=("large_rounded", "almond", "sleepy", "wide"),
    noses=("small", "button", "long", "wide"),
    mouths=("friendly", "small", "wide", "neutral"),
    ears=("default", "small", "large"),
    hair_styles=("short", "bob", "spiky", "long", "bald"),
    skin_presets={
        "fair": "#F6D7C3", "light": "#EBC2A4", "warm_medium": "#D9A07A", "medium": "#C68A62",
        "tan": "#A86F4C", "deep": "#7B4B32", "rich_deep": "#5A3524",
    },
    hair_colors={
        "black": "#151010", "dark_brown": "#3B2416", "brown": "#6B4226", "auburn": "#8A3B1E",
        "blonde": "#D9B66A", "red": "#B2401E", "grey": "#9A9A9A", "white": "#EDEDED",
    },
    eye_colors={"brown": "#5A3A1E", "dark_brown": "#3A2414", "hazel": "#7A5A2E", "green": "#3F7A3A",
                "blue": "#3A6FB0", "grey": "#6E7B85"},
    body_sliders=("shoulder_width", "arm_length", "leg_length", "torso_length", "belly", "hip_width",
                  "hand_size", "foot_size", "neck_length"),
    face_sliders=("brow_height", "cheek_fullness", "jaw_width", "eye_spacing", "chin_length"),
    eye_size=Range(min=0.0, max=1.0, default=0.5),
    skin=ToonMaterial(roughness=0.65, specular=0.25, subsurface=0.05),
    hair=ToonMaterial(roughness=0.55, specular=0.3),
    eyes=ToonMaterial(roughness=0.15, specular=0.5),
    render=RenderSettings(preview_samples=24, preview_resolution=512, focal_length_mm=50),
)

BUILTIN_STYLE_PROFILES: dict[str, StyleProfile] = {FAMILY_CARTOON_V1.ref: FAMILY_CARTOON_V1}


def get_style_profile(ref: str) -> StyleProfile:
    """``family_cartoon@1.0``, or ``family_cartoon`` for the latest built-in version."""
    if ref in BUILTIN_STYLE_PROFILES:
        return BUILTIN_STYLE_PROFILES[ref]
    matches = sorted((p for p in BUILTIN_STYLE_PROFILES.values() if p.id == ref),
                     key=lambda p: tuple(int(x) for x in p.version.split(".")))
    if not matches:
        raise KeyError(f"unknown style profile {ref!r}")
    return matches[-1]
