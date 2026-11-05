"""Animation specification and rig compatibility (SRS 30 Phase 6, architecture 6 and 8).

An animation (``ANIM-001``) is an asset with versions, like a character or an outfit. A version
holds one Action in ``animation.blend`` keyed on the **controls of a rig profile**
(``cartoon_humanoid@1.0``): FK arm controls, IK feet, the torso, chest, neck and head, the
IK/FK switches and the face control's properties. It never keys deform bones and never holds
a character. Because every character of a rig profile has the same controls, a version plays
on any of them; proportions are handled when the Action is applied (location curves are
scaled by the wearer's leg length, see ``AnimationCompat``).

Two sources make a version:

* ``generator``: procedural motion from the built-in generator catalog (``GEN-*@1.0``):
  walk, idle, talk, wave, nod, point, look around and expression takes. The generators
  compute poses on the reference body of a fit profile, so a walk's planted foot really
  stays planted on that body, and the result is baked to keys.
* ``bvh``: a motion-capture file (BVH) retargeted onto the controls through a bone map for
  its skeleton (``cmu``, ``mixamo``): each mapped bone's world rotation change from its rest
  pose is put on the matching control, after the rest poses are matched bone by bone.

Like the other specifications, the check is pure Python so the API, the AI and Blender
agree, and Blender only ever sees ``ResolvedAnimation``. A built version also records its
``AnimationCompat``: what it keys and how far it travels, which the scene validator checks
against a character before anything is loaded.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from studio_contracts.character import Issue, Ref
from studio_contracts.outfit import fit_profiles
from studio_contracts.rig_profile import (
    BUILTIN_RIG_PROFILES,
    CARTOON_HUMANOID_V1,
    RigProfile,
)
from studio_contracts.style_profile import get_style_profile

SCHEMA_VERSION = "animation_spec/1"
GENERATOR_VERSION = "1.0"
FPS = 24  # every library animation is keyed at 24 fps; a scene at 25 or 30 fps scales the strip
MAX_FRAMES = 1200
DEFAULT_REFERENCE_PROFILE = "child_average"

Category = Literal["locomotion", "idle", "gesture", "talk", "expression"]
CATEGORIES: tuple[str, ...] = ("locomotion", "idle", "gesture", "talk", "expression")


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


# ------------------------------------------------------------------ generator catalog
class GeneratorDef(_Frozen):
    id: str = Field(pattern=r"^GEN-[A-Z0-9]+$")
    version: str
    key: str
    label: str
    category: Category
    #: length at 24 fps (a walk derives it from its cadence)
    frames: int
    loops: bool
    #: whether the motion moves the character through the scene (the walk's root travel)
    travels: bool = False
    params: dict[str, ParamDef] = Field(default_factory=dict)
    #: an expression take needs the name of one of the rig profile's expressions
    needs_expression: bool = False

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


GENERATORS: tuple[GeneratorDef, ...] = (
    GeneratorDef(id="GEN-WALK", version=GENERATOR_VERSION, key="walk", label="Walk cycle", category="locomotion",
                 frames=24, loops=True, travels=True, params={
                     "stride": _p(0.4, 1.0, 0.7, "step length as a share of the leg length (hip height)"),
                     "cadence": _p(80.0, 160.0, 120.0, "steps per minute; one cycle is two steps"),
                     "bounce": _p(0.0, 1.0, 0.5, "how much the hips rise and fall"),
                     "arm_swing": _p(0.0, 1.0, 0.5, "how far the arms swing"),
                     "lift": _p(0.0, 1.0, 0.5, "how high the swinging foot lifts"),
                 }),
    GeneratorDef(id="GEN-IDLE", version=GENERATOR_VERSION, key="idle", label="Idle (breathing, weight shift)",
                 category="idle", frames=96, loops=True, params={
                     "breath": _p(0.0, 1.0, 0.5, "breathing depth"),
                     "sway": _p(0.0, 1.0, 0.4, "side to side weight shift"),
                 }),
    GeneratorDef(id="GEN-TALK", version=GENERATOR_VERSION, key="talk", label="Talking gestures", category="talk",
                 frames=96, loops=True, params={
                     "intensity": _p(0.0, 1.0, 0.5, "how big the hand gestures are"),
                     "head": _p(0.0, 1.0, 0.5, "how much the head moves on the beats"),
                     "beats": _p(1.0, 6.0, 3.0, "gesture beats per cycle"),
                 }),
    GeneratorDef(id="GEN-WAVE", version=GENERATOR_VERSION, key="wave", label="Wave (right hand)", category="gesture",
                 frames=48, loops=True, params={
                     "height": _p(0.0, 1.0, 0.6, "how high the hand is raised"),
                     "waves": _p(1.0, 4.0, 2.0, "hand waves per cycle"),
                 }),
    GeneratorDef(id="GEN-NOD", version=GENERATOR_VERSION, key="nod", label="Nod", category="gesture", frames=24,
                 loops=False, params={"depth": _p(0.0, 1.0, 0.5, "how far the head dips")}),
    GeneratorDef(id="GEN-POINT", version=GENERATOR_VERSION, key="point", label="Point forward (left arm)",
                 category="gesture", frames=36, loops=False,
                 params={"height": _p(0.0, 1.0, 0.5, "0 points at the floor ahead, 1 straight ahead")}),
    GeneratorDef(id="GEN-LOOK", version=GENERATOR_VERSION, key="look_around", label="Look around",
                 category="gesture", frames=72, loops=True,
                 params={"range": _p(0.0, 1.0, 0.5, "how far the head turns")}),
    GeneratorDef(id="GEN-EXPR", version=GENERATOR_VERSION, key="expression", label="Expression take",
                 category="expression", frames=48, loops=False, needs_expression=True, params={
                     "intensity": _p(0.0, 1.0, 1.0, "expression weight at the hold"),
                     "body": _p(0.0, 1.0, 0.5, "how much the head and chest act the expression"),
                 }),
)
GENERATOR_CATALOG: dict[str, GeneratorDef] = {g.ref: g for g in GENERATORS}
GENERATOR_BY_KEY: dict[str, GeneratorDef] = {g.key: g for g in GENERATORS}


def get_generator(ref: str) -> GeneratorDef:
    """``GEN-WALK@1.0`` or a catalog key such as ``walk``."""
    if ref in GENERATOR_CATALOG:
        return GENERATOR_CATALOG[ref]
    if ref in GENERATOR_BY_KEY:
        return GENERATOR_BY_KEY[ref]
    raise KeyError(f"no generator {ref!r} in catalog {GENERATOR_VERSION}")


def walk_cycle_frames(cadence: float) -> int:
    """Two steps at ``cadence`` steps per minute, at 24 fps, rounded to an even frame count."""
    frames = 2 * 60.0 * FPS / cadence
    return max(2, int(round(frames / 2.0)) * 2)


# ------------------------------------------------------------------ bone maps
class BoneMap(_Frozen):
    """Source skeleton bone -> rig-profile control. ``root`` is the source's hip bone: its
    translation becomes the torso's (and, for travelling clips, the root's) motion."""

    id: str
    label: str
    target_rig_profile: str
    root: str
    bones: dict[str, str]
    #: source bones whose rotation is folded into their mapped child (e.g. CMU's LowerBack)
    passthrough: tuple[str, ...] = ()


BONE_MAPS: dict[str, BoneMap] = {
    "cmu": BoneMap(
        id="cmu", label="CMU motion capture (cgspeed BVH naming)", target_rig_profile=CARTOON_HUMANOID_V1.ref,
        root="Hips",
        bones={"Hips": "torso", "Spine1": "chest", "Neck1": "neck", "Head": "head",
               **{f"{side}{src}": f"{dst}.{s}" for side, s in (("Left", "L"), ("Right", "R"))
                  for src, dst in (("Arm", "upper_arm_fk"), ("ForeArm", "forearm_fk"), ("Hand", "hand_fk"),
                                   ("UpLeg", "thigh_fk"), ("Leg", "shin_fk"), ("Foot", "foot_fk"))}},
        passthrough=("LowerBack", "Spine", "Neck", "LeftShoulder", "RightShoulder", "LHipJoint", "RHipJoint"),
    ),
    "mixamo": BoneMap(
        id="mixamo", label="Mixamo (mixamorig: naming)", target_rig_profile=CARTOON_HUMANOID_V1.ref,
        root="mixamorig:Hips",
        bones={"mixamorig:Hips": "torso", "mixamorig:Spine2": "chest", "mixamorig:Neck": "neck",
               "mixamorig:Head": "head",
               **{f"mixamorig:{side}{src}": f"{dst}.{s}" for side, s in (("Left", "L"), ("Right", "R"))
                  for src, dst in (("Arm", "upper_arm_fk"), ("ForeArm", "forearm_fk"), ("Hand", "hand_fk"),
                                   ("UpLeg", "thigh_fk"), ("Leg", "shin_fk"), ("Foot", "foot_fk"))}},
        passthrough=("mixamorig:Spine", "mixamorig:Spine1", "mixamorig:LeftShoulder", "mixamorig:RightShoulder"),
    ),
}


# ------------------------------------------------------------------ specification
class GeneratorSource(Model):
    kind: Literal["generator"] = "generator"
    generator: Ref
    params: dict[str, float] = Field(default_factory=dict)
    #: for an expression take: one of the rig profile's expressions
    expression: str | None = None


class BvhSource(Model):
    kind: Literal["bvh"] = "bvh"
    #: work-bucket key of the uploaded file (``uploads/<id>/<name>.bvh``) and its SHA-256
    upload_key: str = Field(pattern=r"^uploads/[0-9a-f-]{36}/[A-Za-z0-9._-]{1,120}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    skeleton: str = Field(description="bone map: cmu or mixamo")
    #: source frames to keep (inclusive, the file's own numbering); the whole take when omitted
    frame_start: int | None = Field(default=None, ge=0)
    frame_end: int | None = Field(default=None, ge=0)
    loops: bool = False
    #: the take moves through space: its forward travel is kept as root motion metadata and
    #: removed from the keys, so a scene places the walk where it wants (in place otherwise)
    travels: bool = False
    #: provenance, shown in the library and kept with the version
    licence: str = Field(min_length=1, max_length=200)
    source_name: str = Field(default="", max_length=200)


Source = Annotated[GeneratorSource | BvhSource, Field(discriminator="kind")]


class AnimationSpecification(Model):
    schema_version: Literal["animation_spec/1"] = SCHEMA_VERSION
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    style_profile: Ref = Field(default_factory=lambda: Ref(id="family_cartoon", version="1.0"))
    rig_profile: Ref | None = None
    #: the generator's category when omitted; required for a BVH source
    category: Category | None = None
    source: Source
    #: body-fit profiles the animation is declared for; empty means every profile of the
    #: style. The first is the reference body it is built and checked on (``child_average``)
    fit_profiles: list[str] = Field(default_factory=list, max_length=16)
    tags: list[str] = Field(default_factory=list, max_length=16)
    seed: int = Field(default=0, ge=0, lt=2**31)


class ResolvedAnimation(_Frozen):
    name: str
    style_profile: str
    rig_profile: str
    category: str
    source_kind: Literal["generator", "bvh"]
    generator: str | None  # GEN-WALK@1.0
    key: str  # generator key, or "bvh"
    params: dict[str, float]
    expression: str | None
    #: frames at 24 fps; None for a BVH source until the file is read
    frames: int | None
    loops: bool
    travels: bool
    bvh: BvhSource | None
    bone_map: BoneMap | None
    fit_profiles: tuple[str, ...]
    reference_fit_profile: str
    tags: tuple[str, ...]
    seed: int

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()

    @property
    def action_name(self) -> str:
        return f"ANIM_{self.key}"


class InvalidAnimation(ValueError):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("; ".join(f"{i.path}: {i.message}" for i in issues if i.severity == "error"))


def validate_animation(spec: AnimationSpecification) -> list[Issue]:
    issues: list[Issue] = []
    try:
        style = get_style_profile(spec.style_profile.ref)
    except KeyError as exc:
        return [Issue(path="style_profile", code="unknown_style_profile", message=str(exc.args[0]))]
    rig_ref = spec.rig_profile.ref if spec.rig_profile else style.rig_profile
    rig = BUILTIN_RIG_PROFILES.get(rig_ref)
    if rig is None:
        issues.append(Issue(path="rig_profile", code="unknown_rig_profile", message=f"no rig profile {rig_ref!r}"))
    src = spec.source
    if isinstance(src, GeneratorSource):
        try:
            gen = get_generator(src.generator.ref)
        except KeyError as exc:
            return [*issues, Issue(path="source.generator", code="unknown_generator", message=str(exc.args[0]))]
        if spec.category is not None and spec.category != gen.category:
            issues.append(Issue(path="category", code="not_for_generator",
                                message=f"a {gen.label} is {gen.category!r}"))
        for name, value in src.params.items():
            d = gen.params.get(name)
            if d is None:
                issues.append(Issue(path=f"source.params.{name}", code="unknown_param",
                                    message=f"a {gen.label} has parameters {sorted(gen.params)}"))
            elif not d.min <= value <= d.max:
                issues.append(Issue(path=f"source.params.{name}", code="out_of_range",
                                    message=f"{value} is outside {d.min}..{d.max}"))
        if gen.needs_expression:
            if src.expression is None:
                issues.append(Issue(path="source.expression", code="required",
                                    message="an expression take needs an expression"))
            elif rig is not None and src.expression not in rig.expressions:
                issues.append(Issue(path="source.expression", code="unknown_expression",
                                    message=f"{src.expression!r} is not one of {list(rig.expressions)}"))
        elif src.expression is not None:
            issues.append(Issue(path="source.expression", code="not_supported", severity="warning",
                                message=f"a {gen.label} has no expression; it is ignored"))
    else:
        if spec.category is None:
            issues.append(Issue(path="category", code="required", message="a BVH animation needs a category"))
        bm = BONE_MAPS.get(src.skeleton)
        if bm is None:
            issues.append(Issue(path="source.skeleton", code="unknown_bone_map",
                                message=f"{src.skeleton!r} is not one of {sorted(BONE_MAPS)}"))
        elif rig is not None and bm.target_rig_profile != rig.ref:
            issues.append(Issue(path="source.skeleton", code="wrong_rig_profile",
                                message=f"bone map {bm.id} targets {bm.target_rig_profile}, not {rig.ref}"))
        if src.frame_start is not None and src.frame_end is not None:
            if src.frame_end <= src.frame_start:
                issues.append(Issue(path="source.frame_end", code="empty_range",
                                    message="frame_end must be after frame_start"))
            elif src.frame_end - src.frame_start + 1 > MAX_FRAMES:
                issues.append(Issue(path="source.frame_end", code="too_long",
                                    message=f"an animation is at most {MAX_FRAMES} frames"))
    known = fit_profiles(style)
    for i, fp in enumerate(spec.fit_profiles):
        if fp not in known:
            issues.append(Issue(path=f"fit_profiles[{i}]", code="unknown_fit_profile",
                                message=f"{fp!r} is not one of {known}"))
    if len(set(spec.fit_profiles)) != len(spec.fit_profiles):
        issues.append(Issue(path="fit_profiles", code="duplicate", message="a fit profile is listed twice"))
    for i, t in enumerate(spec.tags):
        if not t or len(t) > 32 or not t.replace("_", "").isalnum() or t.lower() != t:
            issues.append(Issue(path=f"tags[{i}]", code="bad_tag", message="tags are lower_snake_case, at most 32"))
    return issues


def resolve_animation(spec: AnimationSpecification) -> ResolvedAnimation:
    issues = validate_animation(spec)
    if any(i.severity == "error" for i in issues):
        raise InvalidAnimation(issues)
    style = get_style_profile(spec.style_profile.ref)
    rig = BUILTIN_RIG_PROFILES[spec.rig_profile.ref if spec.rig_profile else style.rig_profile]
    common = dict(name=spec.name, style_profile=style.ref, rig_profile=rig.ref, fit_profiles=tuple(spec.fit_profiles),
                  reference_fit_profile=spec.fit_profiles[0] if spec.fit_profiles else DEFAULT_REFERENCE_PROFILE,
                  tags=tuple(spec.tags), seed=spec.seed)
    src = spec.source
    if isinstance(src, GeneratorSource):
        gen = get_generator(src.generator.ref)
        params = {n: float(src.params.get(n, d.default)) for n, d in gen.params.items()}
        frames = walk_cycle_frames(params["cadence"]) if gen.key == "walk" else gen.frames
        return ResolvedAnimation(**common, category=gen.category, source_kind="generator", generator=gen.ref,
                                 key=gen.key, params=params,
                                 expression=src.expression if gen.needs_expression else None, frames=frames,
                                 loops=gen.loops, travels=gen.travels, bvh=None, bone_map=None)
    return ResolvedAnimation(**common, category=spec.category or "gesture", source_kind="bvh", generator=None,
                             key="bvh", params={}, expression=None, frames=None, loops=src.loops,
                             travels=src.travels, bvh=src, bone_map=BONE_MAPS[src.skeleton])


# ------------------------------------------------------------------ compatibility
class AnimationCompat(_Frozen):
    """What a built animation version needs from a character, measured by the build and
    stored with the version. The scene validator checks it before anything is loaded."""

    rig_profile: str
    category: str
    frames: int
    loops: bool
    travels: bool
    #: pose bones the Action keys (controls only, never DEF bones)
    controls: tuple[str, ...]
    #: face-control properties the Action keys (expr_*, vis_*, eye_*)
    face_props: tuple[str, ...] = ()
    fit_profiles: tuple[str, ...] = ()
    reference_fit_profile: str = DEFAULT_REFERENCE_PROFILE
    #: hip height of the reference body; location keys are scaled by wearer / reference
    reference_leg_length_m: float
    #: forward travel per cycle on the reference body (0 when the animation is in place)
    stride_m: float = 0.0


def animatable(profile: RigProfile) -> set[str]:
    """Pose bones an animation may key on a rig of ``profile``."""
    return {*profile.required_controls, *profile.ik_fk_switches, *profile.animation_controls,
            profile.face_control_bone, "eye_target"}


def compatibility_issues(compat: AnimationCompat, rig_profile_ref: str, fit_profile: str | None) -> list[str]:
    """Why an animation version cannot play on a character with this rig profile and fit
    profile; empty when it can. Same profile id and major version; the character's minor
    version at least the animation's; every keyed control part of the profile."""
    problems: list[str] = []
    a_id, _, a_ver = compat.rig_profile.partition("@")
    c_id, _, c_ver = rig_profile_ref.partition("@")
    try:
        a_major, a_minor = (int(x) for x in a_ver.split("."))
        c_major, c_minor = (int(x) for x in c_ver.split("."))
    except ValueError:
        return [f"rig profile {rig_profile_ref!r} or {compat.rig_profile!r} is not id@major.minor"]
    if a_id != c_id or a_major != c_major:
        problems.append(f"the animation is for rig profile {compat.rig_profile}; the character has {rig_profile_ref}")
    elif c_minor < a_minor:
        problems.append(f"the animation needs {compat.rig_profile} or later; the character has {rig_profile_ref}")
    profile = BUILTIN_RIG_PROFILES.get(rig_profile_ref)
    if profile is not None and not problems:
        missing = sorted(set(compat.controls) - animatable(profile))
        if missing:
            problems.append(f"rig profile {profile.ref} has no controls {missing}")
        missing_props = sorted(set(compat.face_props) - set(profile.face_properties()))
        if missing_props:
            problems.append(f"rig profile {profile.ref} has no face properties {missing_props}")
    if compat.fit_profiles and fit_profile not in compat.fit_profiles:
        problems.append(f"the animation is declared for {list(compat.fit_profiles)}; the character is "
                        f"{fit_profile or 'outside every fit profile'}")
    return problems
