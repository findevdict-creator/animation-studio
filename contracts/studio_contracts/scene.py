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
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from studio_contracts.audio import AUDIO_CODE_RE, MouthCue
from studio_contracts.character import Issue
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


class SceneCamera(Model):
    preset: Literal["wide", "medium", "close"] = "wide"
    #: around the vertical axis from the front (-Y), degrees
    angle_deg: float = Field(default=20.0, ge=-90.0, le=90.0)
    #: frame the instance with this key instead of the whole cast
    focus: str | None = Field(default=None, pattern=INSTANCE_KEY)


class SceneManifest(Model):
    schema_version: Literal["scene_manifest/1"] = SCHEMA_VERSION
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    fps: Literal[24, 25, 30] = 24
    frame_start: int = Field(default=1, ge=0, le=100_000)
    frame_end: int = Field(default=72, ge=1, le=100_000)
    instances: list[CharacterInstance] = Field(min_length=1, max_length=8)
    camera: SceneCamera = Field(default_factory=SceneCamera)

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
class ResolvedPin(_Frozen):
    kind: Literal["character", "outfit", "accessory", "animation"]
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


class ResolvedScene(_Frozen):
    manifest_digest: str
    name: str
    fps: int
    frame_start: int
    frame_end: int
    camera: SceneCamera
    instances: tuple[ResolvedInstance, ...]

    def pins(self) -> list[ResolvedPin]:
        seen: dict[str, ResolvedPin] = {}
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
