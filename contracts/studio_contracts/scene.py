"""Scene manifest (architecture D5, 6.4, 7.2; SRS 16.2, 16.3): the authoritative description
of one scene version, MVP subset.

A scene version pins, per character instance, the exact character version, outfit version and
accessory versions, and holds everything scene-specific: placement, expressions, animation
assignments, dialogue and free-form state. ``scene.blend`` is a build artifact derived from
the manifest; nothing in it flows back into a library asset.

Three layers, like the asset specifications:

* ``SceneManifest`` is what a person or the AI writes: display codes and version labels;
* ``validate_manifest`` is the pure check (structure, frame ranges, the rig profile's
  expressions, the clip catalog); the API adds the registry checks (pins exist and are
  usable, outfit pairs approved, accessories compatible) and resolves the pins;
* ``ResolvedScene`` is what the Blender jobs see: version ids, file hashes and the fixed
  workspace paths ``assets/<kind>/<code>/<version>/`` every scene file links from.

Animation assignments name either a version of the animation library (``ANIM-003@1.0``,
Phase 6: pinned like any other asset, checked for rig compatibility against the character)
or a clip of the built-in Phase 5 stand-in catalog (``CLIP-*@0.1``), which is kept so the
scene versions made with it still validate. Dialogue lines can carry a recording
(``AUDIO-001``); the face track then keys the visemes found in it (lip sync) instead of
the fixed mouth cycle of a text-only line.

Phase 7 adds the set: an ``environment`` version (``ENV-002@1.0``) the scene happens in,
``props`` (published prop versions placed on the floor or on a dressing item's top),
characters started at the environment's named marks, a camera at one of its camera marks,
and a lighting override. Where things stand is checked against the environment's layout
(``check_layout``), the same footprints the environment was validated with; Blender then
measures the assembled scene again.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from studio_contracts.audio import AUDIO_CODE_RE, MouthCue
from studio_contracts.character import Issue
from studio_contracts.environment import (
    CHARACTER_RADIUS_M,
    CLEARANCE_M,
    LIGHTING,
    Rect,
    contains,
    overlap,
)
from studio_contracts.ids import CODE_RE
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1, RigProfile

SCHEMA_VERSION = "scene_manifest/1"
CLIP_CATALOG_VERSION = "0.1"
INSTANCE_KEY = r"^[a-z][a-z0-9_]{0,31}$"
MAX_FRAMES = 2400  # 100 s at 24 fps
NEUTRAL = "neutral"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ------------------------------------------------------------------ clip catalog
class ClipDef(_Frozen):
    id: str = Field(pattern=r"^CLIP-[A-Z0-9]+$")
    version: str
    key: str
    label: str
    frames: int  # length at 24 fps
    loops: bool
    #: rig controls the clip keys (so two clips on different controls can layer)
    controls: tuple[str, ...]

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


CLIPS: tuple[ClipDef, ...] = (
    ClipDef(id="CLIP-IDLE", version=CLIP_CATALOG_VERSION, key="idle", label="Idle breathing", frames=48, loops=True,
            controls=("chest", "head")),
    ClipDef(id="CLIP-WAVE", version=CLIP_CATALOG_VERSION, key="wave", label="Wave (right hand)", frames=36,
            loops=True, controls=("upper_arm_fk.R", "forearm_fk.R")),
    ClipDef(id="CLIP-WALK", version=CLIP_CATALOG_VERSION, key="walk", label="Walk in place", frames=24, loops=True,
            controls=("thigh_fk.L", "thigh_fk.R", "shin_fk.L", "shin_fk.R", "upper_arm_fk.L", "upper_arm_fk.R")),
    ClipDef(id="CLIP-NOD", version=CLIP_CATALOG_VERSION, key="nod", label="Nod", frames=24, loops=False,
            controls=("head",)),
    ClipDef(id="CLIP-POINT", version=CLIP_CATALOG_VERSION, key="point", label="Point forward (left arm)", frames=30,
            loops=False, controls=("upper_arm_fk.L", "forearm_fk.L")),
    ClipDef(id="CLIP-LOOK", version=CLIP_CATALOG_VERSION, key="look_around", label="Look around", frames=48,
            loops=True, controls=("head", "neck")),
)
CLIP_CATALOG: dict[str, ClipDef] = {c.ref: c for c in CLIPS}
CLIP_BY_KEY: dict[str, ClipDef] = {c.key: c for c in CLIPS}


def get_clip(ref: str) -> ClipDef:
    """``CLIP-WAVE@0.1`` or a catalog key such as ``wave``."""
    if ref in CLIP_CATALOG:
        return CLIP_CATALOG[ref]
    if ref in CLIP_BY_KEY:
        return CLIP_BY_KEY[ref]
    raise KeyError(f"no clip {ref!r} in catalog {CLIP_CATALOG_VERSION}")


# ------------------------------------------------------------------ manifest
class Pin(Model):
    """An exact asset version: display code and ``major.minor`` label."""

    code: str = Field(pattern=CODE_RE.pattern)
    version: str = Field(pattern=r"^\d+\.\d+$")

    @property
    def ref(self) -> str:
        return f"{self.code}@{self.version}"


class AccessoryUse(Model):
    accessory: Pin
    #: one of the accessory's attachment points; its default when omitted
    attach_point: str | None = None


Vec3 = tuple[float, float, float]


class Transform(Model):
    location: Vec3 = (0.0, 0.0, 0.0)
    rotation_deg: Vec3 = (0.0, 0.0, 0.0)
    scale: float = Field(default=1.0, ge=0.25, le=4.0)


class ExpressionKey(Model):
    """At ``frame`` the face shows ``expression`` at ``weight`` (every other expression at 0);
    the face eases between keys. ``neutral`` clears every expression."""

    frame: int = Field(ge=0, le=100_000)
    expression: str
    weight: float = Field(default=1.0, ge=0.0, le=1.0)


ANIM_REF_RE = r"^ANIM-\d{3,}@\d+\.\d+$"


def is_library_clip(ref: str) -> bool:
    return re.fullmatch(ANIM_REF_RE, ref) is not None


class AnimationUse(Model):
    clip: str = Field(description="an animation library version (ANIM-003@1.0), or a stand-in catalog clip "
                                  "(CLIP-WAVE@0.1) or catalog key")
    start_frame: int = Field(ge=0, le=100_000)
    repeat: int = Field(default=1, ge=1, le=100)
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    influence: float = Field(default=1.0, ge=0.0, le=1.0)
    #: a travelling animation (a walk) moves the instance forward along its facing while it
    #: plays; false keeps it in place (a walk on the spot)
    travel: bool = True


class DialogueLine(Model):
    """A spoken line. With ``audio`` the mouth follows the visemes found in the recording
    (lip sync), which starts at ``start_frame``; without it the mouth flaps through a fixed
    viseme cycle between the two frames. The text is kept for the script and subtitles and is
    given to the lip-sync analyser as the transcript."""

    start_frame: int = Field(ge=0, le=100_000)
    end_frame: int = Field(ge=0, le=100_000)
    text: str = Field(min_length=1, max_length=500)
    audio: str | None = Field(default=None, pattern=AUDIO_CODE_RE, description="a dialogue recording, AUDIO-NNN")


StateValue = str | float | int | bool


class CharacterInstance(Model):
    key: str = Field(pattern=INSTANCE_KEY, description="unique in the scene, e.g. ali_1")
    character: Pin
    outfit: Pin | None = None
    accessories: list[AccessoryUse] = Field(default_factory=list, max_length=8)
    transform: Transform = Field(default_factory=Transform)
    expressions: list[ExpressionKey] = Field(default_factory=list, max_length=200)
    blink: bool = Field(default=True, description="automatic blinks every few seconds")
    animations: list[AnimationUse] = Field(default_factory=list, max_length=16)
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=50)
    #: scene-specific state that is not geometry (mood, props in hand, notes for the director)
    state: dict[str, StateValue] = Field(default_factory=dict, max_length=16)
    #: start at this mark of the environment (its spot and facing, or a seat); ``transform``
    #: then moves it from there and turns it from the mark's facing
    mark: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")


class SceneCamera(Model):
    """A framing preset around the cast, or (``mark``) one of the environment's camera marks."""

    preset: Literal["wide", "medium", "close"] = "wide"
    mark: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    #: around the vertical axis from the front (-Y), degrees
    angle_deg: float = Field(default=20.0, ge=-90.0, le=90.0)
    #: frame the instance with this key instead of the whole cast
    focus: str | None = Field(default=None, pattern=INSTANCE_KEY)


class PropUse(Model):
    """A published prop version placed in the scene: on the floor at ``at``, or on the top of
    the environment's dressing item (or another scene prop) named by ``on``."""

    key: str = Field(pattern=INSTANCE_KEY, description="unique in the scene, e.g. ball_1")
    prop: Pin
    at: tuple[float, float] = (0.0, 0.0)
    rotation_deg: float = Field(default=0.0, ge=-360, le=360)
    on: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")


class SceneManifest(Model):
    schema_version: Literal["scene_manifest/1"] = SCHEMA_VERSION
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    fps: Literal[24, 25, 30] = 24
    frame_start: int = Field(default=1, ge=0, le=100_000)
    frame_end: int = Field(default=72, ge=1, le=100_000)
    instances: list[CharacterInstance] = Field(min_length=1, max_length=8)
    camera: SceneCamera = Field(default_factory=SceneCamera)
    #: the set; without one the scene is the Phase 5 stage (a floor disc and a three-point rig)
    environment: Pin | None = None
    props: list[PropUse] = Field(default_factory=list, max_length=40)
    #: a lighting preset instead of the environment's own
    lighting: str | None = None

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def validate_manifest(m: SceneManifest, rig: RigProfile = CARTOON_HUMANOID_V1) -> list[Issue]:
    """Everything that can be checked without the registry, all at once. The expression
    names are checked against ``rig``; the API checks them again per character."""
    issues: list[Issue] = []
    if m.frame_end <= m.frame_start:
        issues.append(Issue(path="frame_end", code="empty_range", message="frame_end must be after frame_start"))
    elif m.frame_end - m.frame_start > MAX_FRAMES:
        issues.append(Issue(path="frame_end", code="too_long",
                            message=f"a scene version is at most {MAX_FRAMES} frames"))
    keys: dict[str, int] = {}
    for i, inst in enumerate(m.instances):
        p = f"instances[{i}]"
        if inst.key in keys:
            issues.append(Issue(path=f"{p}.key", code="duplicate",
                                message=f"instances[{keys[inst.key]}] already has key {inst.key!r}"))
        keys.setdefault(inst.key, i)
        if not inst.character.code.startswith("CHAR-"):
            issues.append(Issue(path=f"{p}.character.code", code="wrong_kind", message="a character is CHAR-NNN"))
        if inst.outfit is not None and not inst.outfit.code.startswith("OUTFIT-"):
            issues.append(Issue(path=f"{p}.outfit.code", code="wrong_kind", message="an outfit is OUTFIT-NNN"))
        seen_acc: dict[tuple[str, str | None], int] = {}
        for j, use in enumerate(inst.accessories):
            ap = f"{p}.accessories[{j}]"
            if not use.accessory.code.startswith("ACC-"):
                issues.append(Issue(path=f"{ap}.accessory.code", code="wrong_kind", message="an accessory is ACC-NNN"))
            if use.attach_point is not None and use.attach_point not in rig.attachment_points:
                issues.append(Issue(path=f"{ap}.attach_point", code="no_attachment_point",
                                    message=f"rig profile {rig.ref} has no attachment point {use.attach_point!r}"))
            k = (use.accessory.code, use.attach_point)
            if k in seen_acc:
                issues.append(Issue(path=ap, code="duplicate",
                                    message=f"accessories[{seen_acc[k]}] already puts {use.accessory.code} there"))
            seen_acc.setdefault(k, j)
        names = {NEUTRAL, *rig.expressions}
        frames: dict[int, int] = {}
        for j, e in enumerate(inst.expressions):
            ep = f"{p}.expressions[{j}]"
            if e.expression not in names:
                issues.append(Issue(path=f"{ep}.expression", code="unknown_expression",
                                    message=f"{e.expression!r} is not one of {sorted(names)}"))
            if not m.frame_start <= e.frame <= m.frame_end:
                issues.append(Issue(path=f"{ep}.frame", code="out_of_range",
                                    message=f"frame {e.frame} is outside {m.frame_start}..{m.frame_end}"))
            if e.frame in frames:
                issues.append(Issue(path=f"{ep}.frame", code="duplicate",
                                    message=f"expressions[{frames[e.frame]}] is already keyed at frame {e.frame}"))
            frames.setdefault(e.frame, j)
        for j, a in enumerate(inst.animations):
            ap = f"{p}.animations[{j}]"
            if a.clip.startswith("ANIM-"):
                if not is_library_clip(a.clip):
                    issues.append(Issue(path=f"{ap}.clip", code="bad_ref",
                                        message="a library animation is ANIM-NNN@major.minor"))
            else:
                try:
                    get_clip(a.clip)
                except KeyError as exc:
                    issues.append(Issue(path=f"{ap}.clip", code="unknown_clip", message=str(exc.args[0])))
            if not m.frame_start <= a.start_frame <= m.frame_end:
                issues.append(Issue(path=f"{ap}.start_frame", code="out_of_range",
                                    message=f"frame {a.start_frame} is outside {m.frame_start}..{m.frame_end}"))
        last_end = None
        for j, d in enumerate(sorted(inst.dialogue, key=lambda d: d.start_frame)):
            dp = f"{p}.dialogue[{inst.dialogue.index(d)}]"
            if d.end_frame <= d.start_frame:
                issues.append(Issue(path=f"{dp}.end_frame", code="empty_range", message="a line ends after it starts"))
            if not (m.frame_start <= d.start_frame and d.end_frame <= m.frame_end):
                issues.append(Issue(path=dp, code="out_of_range",
                                    message=f"the line is outside {m.frame_start}..{m.frame_end}"))
            if last_end is not None and d.start_frame < last_end:
                issues.append(Issue(path=dp, code="overlap", message="an instance says one line at a time"))
            last_end = max(last_end or d.end_frame, d.end_frame)
        for name in inst.state:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", name):
                issues.append(Issue(path=f"{p}.state.{name}", code="bad_name",
                                    message="state names are lower_snake_case, at most 40 characters"))
        for axis, v in zip("xyz", inst.transform.location, strict=True):
            if abs(v) > 50:
                issues.append(Issue(path=f"{p}.transform.location.{axis}", code="out_of_range",
                                    message="instances are placed within 50 m of the origin"))
    if m.environment is not None and not m.environment.code.startswith("ENV-"):
        issues.append(Issue(path="environment.code", code="wrong_kind", message="an environment is ENV-NNN"))
    for i, inst in enumerate(m.instances):
        if inst.mark is not None and m.environment is None:
            issues.append(Issue(path=f"instances[{i}].mark", code="no_environment",
                                message="marks belong to an environment; the scene has none"))
    if m.camera.mark is not None and m.environment is None:
        issues.append(Issue(path="camera.mark", code="no_environment",
                            message="camera marks belong to an environment; the scene has none"))
    if m.lighting is not None and m.lighting not in LIGHTING:
        issues.append(Issue(path="lighting", code="unknown_lighting", message=f"lighting is one of {sorted(LIGHTING)}"))
    prop_keys: dict[str, int] = {}
    for i, use in enumerate(m.props):
        p = f"props[{i}]"
        if not use.prop.code.startswith("PROP-"):
            issues.append(Issue(path=f"{p}.prop.code", code="wrong_kind", message="a prop is PROP-NNN"))
        if use.key in prop_keys or use.key in keys:
            issues.append(Issue(path=f"{p}.key", code="duplicate", message=f"{use.key!r} is already used in the scene"))
        prop_keys.setdefault(use.key, i)
        if use.on == use.key:
            issues.append(Issue(path=f"{p}.on", code="self", message="a prop cannot stand on itself"))
        for axis, v in zip("xy", use.at, strict=True):
            if abs(v) > 50:
                issues.append(Issue(path=f"{p}.at.{axis}", code="out_of_range",
                                    message="props are placed within 50 m of the origin"))
    if m.camera.focus is not None and m.camera.focus not in keys:
        issues.append(Issue(path="camera.focus", code="unknown_instance", message=f"no instance {m.camera.focus!r}"))
    # two instances on the same spot is almost always a mistake
    locs = [(inst.key, inst.transform.location) for inst in m.instances]
    for a in range(len(locs)):
        for b in range(a + 1, len(locs)):
            d = sum((x - y) ** 2 for x, y in zip(locs[a][1], locs[b][1], strict=True)) ** 0.5
            if d < 0.35:
                issues.append(Issue(path=f"instances[{b}].transform.location", code="crowded", severity="warning",
                                    message=f"{locs[b][0]} is {d:.2f} m from {locs[a][0]}; they will overlap"))
    return issues


# ------------------------------------------------------------------ resolved (what Blender sees)
# ------------------------------------------------------------------ layout (registry check)
class PropFacts(_Frozen):
    """What placing needs from a prop version (its resolved specification)."""

    size: tuple[float, float, float]
    solid: bool
    rests_on: str
    top_z: float | None = None


def _start(inst: CharacterInstance, marks: dict[str, dict], seats: dict[str, list]) -> tuple[float, float, float]:
    """Where an instance starts on the floor and which way it faces (degrees about Z)."""
    loc, rot = inst.transform.location, inst.transform.rotation_deg[2]
    if inst.mark is None or inst.mark not in marks:
        return loc[0], loc[1], rot
    mk = marks[inst.mark]
    x, y, _ = mk["at"]
    facing = mk["facing_deg"]
    if mk["kind"] == "seat" and mk.get("item") in seats:
        # no sit animation yet: stand just in front of the seat, facing away from it
        _, cx, cy, _w, d, srot, _z = seats[mk["item"]]
        a = math.radians(srot - 90)
        x, y = cx + math.cos(a) * (d / 2 + CHARACTER_RADIUS_M + 0.05), cy + math.sin(a) * (d / 2 + CHARACTER_RADIUS_M + 0.05)
        facing = srot
    return x + loc[0], y + loc[1], facing + rot


def start_transform(inst: CharacterInstance, layout: dict | None) -> Transform:
    """The instance's transform in the scene: its mark's spot and facing, moved and turned by
    its own transform. Without a mark (or an environment) it is its own transform."""
    if layout is None or inst.mark is None:
        return inst.transform
    marks = {mk["name"]: mk for mk in layout.get("marks", [])}
    seats = {s[0]: s for s in layout.get("seats", [])}
    x, y, facing = _start(inst, marks, seats)
    r = inst.transform.rotation_deg
    return Transform(location=(round(x, 4), round(y, 4), inst.transform.location[2]),
                     rotation_deg=(r[0], r[1], round(facing, 4)), scale=inst.transform.scale)


def check_layout(m: SceneManifest, layout: dict | None, props: dict[str, PropFacts]) -> list[Issue]:
    """Where things stand, against the environment's layout (``ResolvedEnvironment.layout``):
    marks and camera marks exist; characters start on free floor inside the set; props stand
    inside it, on the floor clear of solid dressing and of each other, or on a top. ``props``
    maps each prop ref (``PROP-001@1.0``) to its facts; refs missing from it are skipped
    (the registry check reports them). Paths through the set are measured in Blender."""
    issues: list[Issue] = []
    marks = {mk["name"]: mk for mk in (layout or {}).get("marks", [])}
    cams = {c["name"] for c in (layout or {}).get("cameras", [])}
    seats = {s[0]: s for s in (layout or {}).get("seats", [])}
    solids: list[tuple[str, Rect]] = [(s[0], tuple(s[1:6])) for s in (layout or {}).get("solids", [])]
    tops: dict[str, tuple[Rect, float]] = {t[0]: (tuple(t[1:6]), t[6]) for t in (layout or {}).get("tops", [])}
    size = (layout or {}).get("size")
    indoor = (layout or {}).get("kind") == "room"

    def inside(r: Rect, margin: float) -> bool:
        from studio_contracts.environment import inside_bounds
        return size is None or inside_bounds(r, size[0], size[1], margin)

    if layout is not None:
        if m.camera.mark is not None and m.camera.mark not in cams:
            issues.append(Issue(path="camera.mark", code="unknown_mark",
                                message=f"the environment has camera marks {sorted(cams)}"))
        for i, inst in enumerate(m.instances):
            if inst.mark is not None and inst.mark not in marks:
                issues.append(Issue(path=f"instances[{i}].mark", code="unknown_mark",
                                    message=f"the environment has marks {sorted(marks)}"))
            elif inst.mark is not None and marks[inst.mark]["kind"] == "seat":
                issues.append(Issue(path=f"instances[{i}].mark", code="no_sit_animation", severity="warning",
                                    message="there is no sit animation yet; the character stands in front of the seat"))

    placed: list[tuple[str, Rect, bool]] = []  # scene props on the floor: key, footprint, solid
    for i, use in enumerate(m.props):
        f = props.get(use.prop.ref)
        if f is None:
            continue
        p = f"props[{i}]"
        rect: Rect = (use.at[0], use.at[1], f.size[0], f.size[1], use.rotation_deg)
        if use.on is None:
            if f.rests_on == "top":
                issues.append(Issue(path=f"{p}.on", code="needs_surface",
                                    message="this prop goes on top of something; name it with 'on'"))
            if not inside(rect, CLEARANCE_M):
                issues.append(Issue(path=f"{p}.at", code="outside", message="the prop is not inside the set"))
            for name, other in solids:
                if overlap(rect, other, CLEARANCE_M):
                    issues.append(Issue(path=f"{p}.at", code="overlap", message=f"it overlaps the set's {name}"))
            for name, other, solid in placed:
                if (solid or f.solid) and overlap(rect, other, CLEARANCE_M):
                    issues.append(Issue(path=f"{p}.at", code="overlap", message=f"it overlaps {name}"))
            placed.append((use.key, rect, f.solid))
            if f.top_z is not None:
                tops[use.key] = (rect, f.top_z)
    for i, use in enumerate(m.props):
        if use.on is None or use.prop.ref not in props:
            continue
        base = tops.get(use.on)
        if base is None:
            issues.append(Issue(path=f"props[{i}].on", code="no_surface",
                                message=f"no dressing item or scene prop {use.on!r} with a top"))
        elif not contains(base[0], use.at[0], use.at[1]):
            issues.append(Issue(path=f"props[{i}].at", code="off_surface", message=f"it is not on {use.on}'s top"))

    for i, inst in enumerate(m.instances):
        if inst.mark is not None and inst.mark not in marks:
            continue
        x, y, _ = _start(inst, marks, seats)
        p = f"instances[{i}]"
        if size is not None and (abs(x) > size[0] / 2 - CHARACTER_RADIUS_M or abs(y) > size[1] / 2 - CHARACTER_RADIUS_M):
            issues.append(Issue(path=f"{p}.transform.location", code="outside",
                                message=f"{inst.key} starts at ({x:.2f}, {y:.2f}), outside the "
                                        f"{'room' if indoor else 'set'}"))
            continue
        for name, rect in solids:
            if contains(rect, x, y, CHARACTER_RADIUS_M - 0.05):
                issues.append(Issue(path=f"{p}.transform.location", code="blocked",
                                    message=f"{inst.key} starts inside the set's {name}"))
        for name, rect, solid in placed:
            if solid and contains(rect, x, y, CHARACTER_RADIUS_M - 0.05):
                issues.append(Issue(path=f"{p}.transform.location", code="blocked",
                                    message=f"{inst.key} starts inside {name}"))
    return issues


class ResolvedPin(_Frozen):
    kind: Literal["character", "outfit", "accessory", "animation", "prop", "environment"]
    code: str
    version: str
    version_id: str
    status: str
    name: str
    #: workspace directory the files are staged in: assets/<kind>/<code>/<version>
    path: str
    #: file name -> SHA-256 (character.blend, character.json / outfit.blend / accessory.blend /
    #: animation.blend)
    files: dict[str, str]

    @property
    def ref(self) -> str:
        return f"{self.code}@{self.version}"


class ResolvedAccessoryUse(_Frozen):
    pin: ResolvedPin
    attach_point: str
    attach_bone: str
    item: str
    placement: str


class ResolvedClipUse(_Frozen):
    clip: str  # ANIM-003@1.0 or CLIP-X@0.1
    key: str
    start_frame: int
    repeat: int
    speed: float
    influence: float
    frames: int
    loops: bool
    source: Literal["catalog", "library"] = "catalog"
    #: library animations: the pinned version and its compatibility record (AnimationCompat)
    pin: ResolvedPin | None = None
    compat: dict | None = None
    travel: bool = False


class ResolvedAudio(_Frozen):
    code: str  # AUDIO-001
    sha256: str
    #: workspace path the normalised WAV is staged at: audio/<code>/dialogue.wav
    path: str
    duration_s: float
    analyser: str
    cues: tuple[MouthCue, ...]


class ResolvedDialogueLine(_Frozen):
    start_frame: int
    end_frame: int
    text: str
    audio: ResolvedAudio | None = None


class ResolvedInstance(_Frozen):
    key: str
    character: ResolvedPin
    rig_profile: str
    fit_profile: str | None
    outfit: ResolvedPin | None
    #: the compatibility record that allowed the pair (approved, or verified with an override)
    outfit_compatibility: dict | None
    accessories: tuple[ResolvedAccessoryUse, ...]
    transform: Transform
    expressions: tuple[ExpressionKey, ...]
    blink: bool
    animations: tuple[ResolvedClipUse, ...]
    dialogue: tuple[ResolvedDialogueLine, ...]
    state: dict[str, StateValue]


class ResolvedPropUse(_Frozen):
    key: str
    pin: ResolvedPin
    item: str  # PROPD-CHAIR@0.1
    size: tuple[float, float, float]
    at: tuple[float, float]
    #: height it stands at: 0 on the floor, the top it is on otherwise
    z: float
    rotation_deg: float
    on: str | None


class ResolvedScene(_Frozen):
    manifest_digest: str
    name: str
    fps: int
    frame_start: int
    frame_end: int
    camera: SceneCamera
    instances: tuple[ResolvedInstance, ...]
    environment: ResolvedPin | None = None
    #: the environment version's layout (``ResolvedEnvironment.layout``)
    layout: dict | None = None
    props: tuple[ResolvedPropUse, ...] = ()
    #: the lighting preset in force: the manifest's override, else the environment's
    lighting: str | None = None

    def pins(self) -> list[ResolvedPin]:
        seen: dict[str, ResolvedPin] = {}
        for p in (self.environment, *(u.pin for u in self.props)):
            if p is not None:
                seen.setdefault(p.version_id, p)
        for inst in self.instances:
            for p in (inst.character, inst.outfit, *(a.pin for a in inst.accessories),
                      *(c.pin for c in inst.animations)):
                if p is not None:
                    seen.setdefault(p.version_id, p)
        return sorted(seen.values(), key=lambda p: (p.kind, p.code, p.version))

    def audio(self) -> list[ResolvedAudio]:
        seen: dict[str, ResolvedAudio] = {}
        for inst in self.instances:
            for d in inst.dialogue:
                if d.audio is not None:
                    seen.setdefault(d.audio.code, d.audio)
        return sorted(seen.values(), key=lambda a: a.code)


def pin_path(kind: str, code: str, version: str) -> str:
    return f"assets/{kind}/{code}/{version}"


def audio_path(code: str) -> str:
    return f"audio/{code}/dialogue.wav"


def resolve_clip(use: AnimationUse) -> ResolvedClipUse:
    """A stand-in catalog clip; library animations are resolved by the API against the registry."""
    c = get_clip(use.clip)
    return ResolvedClipUse(clip=c.ref, key=c.key, start_frame=use.start_frame, repeat=use.repeat, speed=use.speed,
                           influence=use.influence, frames=c.frames, loops=c.loops)
