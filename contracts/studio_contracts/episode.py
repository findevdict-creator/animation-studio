"""Series, scripts, storyboards and episode versions (SRS 13 to 18, architecture 7.3).

Four documents, each validated by pure code here and checked against the registry by the API:

* ``SeriesSettings``: the series' style profile and its render configuration (SRS 18: one
  colour management for the whole series), and each cast member's voice (SRS 17: voice
  consistency across episodes);
* ``ScriptSpecification`` (``script/1``): the structured script of SRS 14.1. Characters and
  locations are named by display code (``CHAR-001``, ``ENV-001``), never only by name;
* ``StoryboardSpecification`` (``storyboard/1``): shots (SRS 15), each with its camera
  (type, camera mark, angle, movement), duration, character placement, actions, dialogue,
  environment, lighting, animation references and transition;
* ``EpisodeManifest`` (``episode/1``): what an episode version freezes: the script and
  storyboard it was made from, the cast (exact character, outfit and accessory versions
  and voices), one scene version per shot, music and sound effects, and the render settings.

``plan_storyboard`` turns a script into a first storyboard without AI (an establishing
shot per scene, then a shot per line on the speaker); the AI can suggest another, and a
person edits either. ``shot_to_scene`` compiles one shot into a ``SceneManifest`` with
the cast's pins, so every shot is an ordinary scene version: assembled, reopened and
rendered by the Phase 5 to 7 machinery.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from studio_contracts.audio import AUDIO_CODE_RE
from studio_contracts.character import Issue, Ref
from studio_contracts.environment import LIGHTING
from studio_contracts.rig_profile import CARTOON_HUMANOID_V1
from studio_contracts.scene import (
    INSTANCE_KEY,
    NEUTRAL,
    AccessoryUse,
    AnimationUse,
    CharacterInstance,
    DialogueLine,
    ExpressionKey,
    Pin,
    SceneCamera,
    SceneManifest,
    Transform,
    get_clip,
    is_library_clip,
)

SCRIPT_SCHEMA = "script/1"
STORYBOARD_SCHEMA = "storyboard/1"
EPISODE_SCHEMA = "episode/1"
CHAR_RE = r"^CHAR-\d{3,}$"
ENV_RE = r"^ENV-\d{3,}$"
SCENE_ID_RE = r"^SC-\d{3,}$"
SHOT_ID_RE = r"^SH-\d{3,}$"
SCENE_CODE_RE = r"^SCENE-\d{3,}$"
MAX_SHOT_S = 30.0
MAX_EPISODE_S = 1800.0

#: script actions the built-in clip catalog can play; an episode can map any action to a
#: library animation instead (``EpisodeManifest.action_animations``)
ACTION_CLIPS: dict[str, str] = {"idle": "idle", "stand": "idle", "wave": "wave", "walk": "walk", "nod": "nod",
                                "point": "point", "look_around": "look_around", "look": "look_around"}
EXPRESSIONS = (NEUTRAL, *CARTOON_HUMANOID_V1.expressions)
#: seconds a line takes when it has no recording yet: words at a child's speaking pace
WORDS_PER_S = 2.6


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def line_seconds(text: str) -> float:
    """An estimate of how long ``text`` takes to say (before text to speech gives the real one)."""
    words = len(re.findall(r"[\w']+", text))
    return round(max(0.8, words / WORDS_PER_S + 0.3), 2)


# ------------------------------------------------------------------ series
class RenderSettings(Model):
    """SRS 18's initial targets, all configurable. Colour management is a series setting so
    every episode of a series looks the same."""

    width: int = Field(default=1920, ge=64, le=3840)
    height: int = Field(default=1080, ge=36, le=2160)
    fps: Literal[24, 25, 30] = 24
    #: Cycles by default: Eevee needs a GPU or EGL, which the CPU workers do not have
    engine: Literal["eevee", "cycles"] = "cycles"
    samples: int = Field(default=12, ge=1, le=4096)
    view_transform: Literal["Standard", "Filmic", "AgX"] = "Standard"
    look: str = Field(default="None", max_length=40)
    exposure: float = Field(default=0.0, ge=-10, le=10)
    gamma: float = Field(default=1.0, ge=0.1, le=5)
    frame_format: Literal["PNG"] = "PNG"
    video_codec: Literal["h264"] = "h264"
    crf: int = Field(default=18, ge=0, le=51, description="H.264 quality: lower is better")
    audio_codec: Literal["aac"] = "aac"
    audio_bitrate_k: int = Field(default=192, ge=64, le=512)


class VoiceProfile(Model):
    """How a character sounds (SRS 17). The provider is named, never assumed; the episode
    freezes the profile with each line it synthesised."""

    provider: str = Field(default="espeak-ng", pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    voice: str = Field(default="en-us", max_length=64)
    rate_wpm: int = Field(default=160, ge=80, le=320)
    pitch: int = Field(default=50, ge=0, le=99)
    variant: str | None = Field(default=None, max_length=32, description="espeak-ng: m1..m7, f1..f5, …")


class SeriesSettings(Model):
    style_profile: Ref = Field(default_factory=lambda: Ref(id="family_cartoon", version="1.0"))
    render: RenderSettings = Field(default_factory=RenderSettings)


# ------------------------------------------------------------------ script (SRS 14.1)
class ScriptEvent(Model):
    character_id: str = Field(pattern=CHAR_RE)
    action: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$", description="walk, wave, nod, point, look_around, …")
    target: str | None = Field(default=None, max_length=80, description="what the action is aimed at, free text")
    emotion: str | None = None


class ScriptLine(Model):
    character_id: str = Field(pattern=CHAR_RE)
    text: str = Field(min_length=1, max_length=500)
    emotion: str | None = Field(default=None, description="one of the rig's expressions")


class ScriptScene(Model):
    scene_id: str = Field(pattern=SCENE_ID_RE)
    location_id: str | None = Field(default=None, pattern=ENV_RE)
    description: str = Field(default="", max_length=2000)
    emotional_context: str = Field(default="", max_length=400)
    characters: list[Annotated[str, Field(pattern=CHAR_RE)]] = Field(min_length=1, max_length=8)
    events: list[ScriptEvent] = Field(default_factory=list, max_length=40)
    dialogue: list[ScriptLine] = Field(default_factory=list, max_length=80)
    estimated_duration_s: float | None = Field(default=None, gt=0, le=600)


class ScriptSpecification(Model):
    schema_version: Literal["script/1"] = SCRIPT_SCHEMA
    episode_id: str | None = Field(default=None, pattern=r"^EP-\d{3,}$")
    title: str = Field(min_length=1, max_length=120)
    synopsis: str = Field(default="", max_length=4000)
    scenes: list[ScriptScene] = Field(min_length=1, max_length=40)
    estimated_duration_s: float | None = Field(default=None, gt=0, le=MAX_EPISODE_S)

    def characters(self) -> list[str]:
        return sorted({c for s in self.scenes for c in s.characters})

    def locations(self) -> list[str]:
        return sorted({s.location_id for s in self.scenes if s.location_id})

    def duration_s(self) -> float:
        return round(sum(s.estimated_duration_s or sum(line_seconds(d.text) for d in s.dialogue) + 2.0
                         for s in self.scenes), 2)


def validate_script(spec: ScriptSpecification, known_characters: set[str] | None = None,
                    known_environments: set[str] | None = None) -> list[Issue]:
    """Structure, references inside the script, and (when given) that every character and
    location exists. The API passes the published codes it found."""
    issues: list[Issue] = []
    seen: dict[str, int] = {}
    for i, sc in enumerate(spec.scenes):
        p = f"scenes[{i}]"
        if sc.scene_id in seen:
            issues.append(Issue(path=f"{p}.scene_id", code="duplicate", message=f"scenes[{seen[sc.scene_id]}] is {sc.scene_id}"))
        seen.setdefault(sc.scene_id, i)
        if len(set(sc.characters)) != len(sc.characters):
            issues.append(Issue(path=f"{p}.characters", code="duplicate", message="a character is listed twice"))
        for c in sc.characters:
            if known_characters is not None and c not in known_characters:
                issues.append(Issue(path=f"{p}.characters", code="not_found", message=f"{c} is not a published character"))
        if sc.location_id and known_environments is not None and sc.location_id not in known_environments:
            issues.append(Issue(path=f"{p}.location_id", code="not_found",
                                message=f"{sc.location_id} is not a published environment"))
        for j, ev in enumerate(sc.events):
            if ev.character_id not in sc.characters:
                issues.append(Issue(path=f"{p}.events[{j}].character_id", code="not_in_scene",
                                    message=f"{ev.character_id} is not one of the scene's characters"))
            if ev.action not in ACTION_CLIPS:
                issues.append(Issue(path=f"{p}.events[{j}].action", code="no_clip", severity="warning",
                                    message=f"no built-in clip plays {ev.action!r}; map it to a library animation "
                                            f"or it is left out (built in: {sorted(ACTION_CLIPS)})"))
            if ev.emotion is not None and ev.emotion not in EXPRESSIONS:
                issues.append(Issue(path=f"{p}.events[{j}].emotion", code="unknown_expression",
                                    message=f"{ev.emotion!r} is not one of {list(EXPRESSIONS)}"))
        for j, ln in enumerate(sc.dialogue):
            if ln.character_id not in sc.characters:
                issues.append(Issue(path=f"{p}.dialogue[{j}].character_id", code="not_in_scene",
                                    message=f"{ln.character_id} is not one of the scene's characters"))
            if ln.emotion is not None and ln.emotion not in EXPRESSIONS:
                issues.append(Issue(path=f"{p}.dialogue[{j}].emotion", code="unknown_expression",
                                    message=f"{ln.emotion!r} is not one of {list(EXPRESSIONS)}"))
    if spec.duration_s() > MAX_EPISODE_S:
        issues.append(Issue(path="scenes", code="too_long", message=f"an episode is at most {MAX_EPISODE_S:.0f} s"))
    return issues


# ------------------------------------------------------------------ storyboard (SRS 15)
class ShotCamera(Model):
    type: Literal["wide", "medium", "close"] = "wide"
    #: one of the environment's camera marks instead of framing the cast
    mark: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    angle_deg: float = Field(default=15.0, ge=-90, le=90)
    #: frame this character (CHAR code) instead of everyone in the shot
    focus: str | None = Field(default=None, pattern=CHAR_RE)
    movement: Literal["static", "push_in", "pull_out", "pan_left", "pan_right"] = "static"


class ShotPlacement(Model):
    character_id: str = Field(pattern=CHAR_RE)
    mark: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    #: metres from the floor centre (with a mark: from the mark)
    at: tuple[float, float] = (0.0, 0.0)
    facing_deg: float = Field(default=0.0, ge=-360, le=360)
    emotion: str | None = None


class ShotAction(Model):
    character_id: str = Field(pattern=CHAR_RE)
    action: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    start_s: float = Field(default=0.0, ge=0, le=MAX_SHOT_S)
    #: an exact animation: a library version (ANIM-011@1.0) or a built-in clip; else the
    #: episode's mapping for the action, else the built-in clip for it
    animation: str | None = Field(default=None, max_length=40)
    repeat: int = Field(default=1, ge=1, le=50)
    travel: bool = True


class ShotLine(Model):
    character_id: str = Field(pattern=CHAR_RE)
    text: str = Field(min_length=1, max_length=500)
    start_s: float = Field(default=0.2, ge=0, le=MAX_SHOT_S)
    emotion: str | None = None
    #: the recording (text to speech or an upload) once there is one
    audio: str | None = Field(default=None, pattern=AUDIO_CODE_RE)


class Transition(Model):
    kind: Literal["cut", "fade", "dissolve"] = "cut"
    duration_s: float = Field(default=0.0, ge=0, le=3)


class Shot(Model):
    shot_id: str = Field(pattern=SHOT_ID_RE)
    scene_id: str = Field(pattern=SCENE_ID_RE, description="the script scene it belongs to")
    description: str = Field(default="", max_length=2000)
    duration_s: float = Field(gt=0.4, le=MAX_SHOT_S)
    environment: str | None = Field(default=None, pattern=ENV_RE)
    lighting: str | None = None
    camera: ShotCamera = Field(default_factory=ShotCamera)
    placement: list[ShotPlacement] = Field(min_length=1, max_length=8)
    actions: list[ShotAction] = Field(default_factory=list, max_length=24)
    dialogue: list[ShotLine] = Field(default_factory=list, max_length=24)
    #: how this shot comes in from the one before (the first shot fades in from black)
    transition_in: Transition = Field(default_factory=Transition)


class StoryboardSpecification(Model):
    schema_version: Literal["storyboard/1"] = STORYBOARD_SCHEMA
    shots: list[Shot] = Field(min_length=1, max_length=200)

    def duration_s(self) -> float:
        """The running time: a dissolve overlaps the shot before it by its length."""
        t = 0.0
        for i, s in enumerate(self.shots):
            start = t
            if i > 0 and s.transition_in.kind == "dissolve":
                start = t - min(s.transition_in.duration_s, self.shots[i - 1].duration_s, s.duration_s)
            t = start + s.duration_s
        return round(t, 3)


def line_span(line: ShotLine, audio_s: dict[str, float] | None = None) -> float:
    if line.audio and audio_s and line.audio in audio_s:
        return audio_s[line.audio]
    return line_seconds(line.text)


def validate_storyboard(sb: StoryboardSpecification, script: ScriptSpecification | None = None,
                        audio_s: dict[str, float] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    seen: dict[str, int] = {}
    scenes = {s.scene_id: s for s in script.scenes} if script else None
    for i, sh in enumerate(sb.shots):
        p = f"shots[{i}]"
        if sh.shot_id in seen:
            issues.append(Issue(path=f"{p}.shot_id", code="duplicate", message=f"shots[{seen[sh.shot_id]}] is {sh.shot_id}"))
        seen.setdefault(sh.shot_id, i)
        if scenes is not None and sh.scene_id not in scenes:
            issues.append(Issue(path=f"{p}.scene_id", code="not_in_script", message=f"the script has no {sh.scene_id}"))
        cast = [pl.character_id for pl in sh.placement]
        if len(set(cast)) != len(cast):
            issues.append(Issue(path=f"{p}.placement", code="duplicate", message="a character is placed twice"))
        if sh.lighting is not None and sh.lighting not in LIGHTING:
            issues.append(Issue(path=f"{p}.lighting", code="unknown_lighting", message=f"lighting is one of {sorted(LIGHTING)}"))
        if sh.environment is None:
            if sh.camera.mark:
                issues.append(Issue(path=f"{p}.camera.mark", code="no_environment", message="camera marks need an environment"))
            for j, pl in enumerate(sh.placement):
                if pl.mark:
                    issues.append(Issue(path=f"{p}.placement[{j}].mark", code="no_environment",
                                        message="marks need an environment"))
        if sh.camera.focus is not None and sh.camera.focus not in cast:
            issues.append(Issue(path=f"{p}.camera.focus", code="not_in_shot", message=f"{sh.camera.focus} is not placed"))
        for j, pl in enumerate(sh.placement):
            if pl.emotion is not None and pl.emotion not in EXPRESSIONS:
                issues.append(Issue(path=f"{p}.placement[{j}].emotion", code="unknown_expression",
                                    message=f"{pl.emotion!r} is not one of {list(EXPRESSIONS)}"))
        for j, a in enumerate(sh.actions):
            if a.character_id not in cast:
                issues.append(Issue(path=f"{p}.actions[{j}].character_id", code="not_in_shot",
                                    message=f"{a.character_id} is not placed in the shot"))
            if a.start_s >= sh.duration_s:
                issues.append(Issue(path=f"{p}.actions[{j}].start_s", code="out_of_range", message="it starts after the shot ends"))
            if a.animation is not None and not is_library_clip(a.animation):
                try:
                    get_clip(a.animation)
                except KeyError as exc:
                    issues.append(Issue(path=f"{p}.actions[{j}].animation", code="unknown_clip", message=str(exc.args[0])))
        ends: dict[str, float] = {}
        for ln in sorted(sh.dialogue, key=lambda x: x.start_s):
            jp = f"{p}.dialogue[{sh.dialogue.index(ln)}]"
            if ln.character_id not in cast:
                issues.append(Issue(path=f"{jp}.character_id", code="not_in_shot",
                                    message=f"{ln.character_id} is not placed in the shot"))
            if ln.emotion is not None and ln.emotion not in EXPRESSIONS:
                issues.append(Issue(path=f"{jp}.emotion", code="unknown_expression",
                                    message=f"{ln.emotion!r} is not one of {list(EXPRESSIONS)}"))
            end = ln.start_s + line_span(ln, audio_s)
            if end > sh.duration_s + 1e-6:
                issues.append(Issue(path=jp, code="too_long",
                                    message=f"the line runs to {end:.2f} s; the shot is {sh.duration_s:.2f} s"))
            if ln.start_s < ends.get(ln.character_id, -1.0):
                issues.append(Issue(path=jp, code="overlap", message="a character says one line at a time"))
            ends[ln.character_id] = max(ends.get(ln.character_id, 0.0), end)
    if sb.duration_s() > MAX_EPISODE_S:
        issues.append(Issue(path="shots", code="too_long", message=f"an episode is at most {MAX_EPISODE_S:.0f} s"))
    return issues


def plan_storyboard(script: ScriptSpecification, marks: dict[str, list[str]] | None = None) -> StoryboardSpecification:
    """A first storyboard by rule, no AI: per script scene, a wide establishing shot with
    the scene's events, then one shot per line framing the speaker (medium, then close for
    a second line in a row from the same speaker); each scene after the first comes in with
    a short fade. ``marks`` maps an environment code to its spawn marks, so characters start
    on them in order; without marks they stand in a row."""
    shots: list[Shot] = []
    n = 0

    def nid() -> str:
        nonlocal n
        n += 1
        return f"SH-{n:03d}"

    for si, sc in enumerate(script.scenes):
        env_marks = (marks or {}).get(sc.location_id or "", [])
        placement = []
        for k, c in enumerate(sc.characters):
            if k < len(env_marks):
                placement.append(ShotPlacement(character_id=c, mark=env_marks[k]))
            else:
                x = (k - (len(sc.characters) - 1) / 2) * 0.9
                placement.append(ShotPlacement(character_id=c, at=(round(x, 2), 0.0)))
        acts = []
        for ev in sc.events:
            acts.append(ShotAction(character_id=ev.character_id, action=ev.action))
        est = max(3.0, 1.0 + 1.6 * len({a.character_id for a in acts})) if acts or not sc.dialogue else 2.5
        fade = Transition(kind="fade", duration_s=0.5) if si > 0 else Transition()
        shots.append(Shot(shot_id=nid(), scene_id=sc.scene_id, description=sc.description or f"{sc.scene_id} establishing",
                          duration_s=round(est, 2), environment=sc.location_id,
                          camera=ShotCamera(type="wide", mark="wide" if sc.location_id else None),
                          placement=placement, actions=acts, transition_in=fade))
        last = None
        for ln in sc.dialogue:
            span = line_seconds(ln.text)
            kind = "close" if ln.character_id == last else "medium"
            last = ln.character_id
            line_pl = [pl.model_copy(update={"emotion": ln.emotion} if pl.character_id == ln.character_id and ln.emotion
                                     else {}) for pl in placement]
            shots.append(Shot(shot_id=nid(), scene_id=sc.scene_id, description=f"{ln.character_id}: {ln.text[:80]}",
                              duration_s=round(min(MAX_SHOT_S, span + 0.8), 2), environment=sc.location_id,
                              camera=ShotCamera(type=kind, focus=ln.character_id),
                              placement=line_pl,
                              dialogue=[ShotLine(character_id=ln.character_id, text=ln.text, start_s=0.3,
                                                 emotion=ln.emotion)]))
    return StoryboardSpecification(shots=shots)


# ------------------------------------------------------------------ episode
class CastMember(Model):
    """A character as the episode uses it: exact versions and its voice."""

    key: str = Field(pattern=INSTANCE_KEY, description="instance key in every scene, e.g. ali")
    character: Pin
    outfit: Pin | None = None
    accessories: list[AccessoryUse] = Field(default_factory=list, max_length=8)
    voice: VoiceProfile = Field(default_factory=VoiceProfile)


class EpisodeShot(Model):
    shot_id: str = Field(pattern=SHOT_ID_RE)
    scene: str = Field(pattern=SCENE_CODE_RE)
    version: int = Field(ge=1)
    transition_in: Transition = Field(default_factory=Transition)


class AudioCue(Model):
    """Music or a sound effect on the episode timeline (SRS 17)."""

    audio: str = Field(pattern=AUDIO_CODE_RE)
    start_s: float = Field(default=0.0, ge=0, le=MAX_EPISODE_S)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    fade_in_s: float = Field(default=0.0, ge=0, le=10)
    fade_out_s: float = Field(default=0.0, ge=0, le=10)


class EpisodeManifest(Model):
    schema_version: Literal["episode/1"] = EPISODE_SCHEMA
    title: str = Field(min_length=1, max_length=120)
    script_version: int | None = Field(default=None, ge=1)
    storyboard: int | None = Field(default=None, ge=1)
    cast: list[CastMember] = Field(min_length=1, max_length=12)
    #: action name -> exact animation (ANIM-011@1.0 or a built-in clip) for every shot
    action_animations: dict[str, str] = Field(default_factory=dict, max_length=40)
    #: environment code -> exact version
    environments: list[Pin] = Field(default_factory=list, max_length=20)
    shots: list[EpisodeShot] = Field(default_factory=list, max_length=200)
    music: list[AudioCue] = Field(default_factory=list, max_length=8)
    sfx: list[AudioCue] = Field(default_factory=list, max_length=100)
    dialogue_volume: float = Field(default=1.0, ge=0, le=2)
    render: RenderSettings = Field(default_factory=RenderSettings)

    def member(self, code: str) -> CastMember | None:
        return next((c for c in self.cast if c.character.code == code), None)


def validate_episode(m: EpisodeManifest) -> list[Issue]:
    issues: list[Issue] = []
    keys: set[str] = set()
    codes: set[str] = set()
    for i, c in enumerate(m.cast):
        if c.key in keys or c.character.code in codes:
            issues.append(Issue(path=f"cast[{i}]", code="duplicate", message="a character or key is cast twice"))
        keys.add(c.key)
        codes.add(c.character.code)
        if not c.character.code.startswith("CHAR-"):
            issues.append(Issue(path=f"cast[{i}].character.code", code="wrong_kind", message="a character is CHAR-NNN"))
    for i, e in enumerate(m.environments):
        if not e.code.startswith("ENV-"):
            issues.append(Issue(path=f"environments[{i}].code", code="wrong_kind", message="an environment is ENV-NNN"))
    if len({e.code for e in m.environments}) != len(m.environments):
        issues.append(Issue(path="environments", code="duplicate", message="an environment is pinned twice"))
    for action, ref in m.action_animations.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", action):
            issues.append(Issue(path=f"action_animations.{action}", code="bad_name", message="actions are lower_snake_case"))
        if not is_library_clip(ref):
            try:
                get_clip(ref)
            except KeyError as exc:
                issues.append(Issue(path=f"action_animations.{action}", code="unknown_clip", message=str(exc.args[0])))
    if len({s.shot_id for s in m.shots}) != len(m.shots):
        issues.append(Issue(path="shots", code="duplicate", message="a shot is listed twice"))
    if m.render.width % 2 or m.render.height % 2:
        issues.append(Issue(path="render", code="odd_size", message="H.264 needs an even width and height"))
    return issues


def shot_to_scene(shot: Shot, m: EpisodeManifest, audio_s: dict[str, float] | None = None,
                  name: str | None = None) -> tuple[SceneManifest | None, list[Issue]]:
    """One shot as a scene manifest with the cast's exact pins. Seconds become frames at the
    episode's fps (frame 1 is the shot's first frame); actions become animation assignments
    (an explicit animation, else the episode's mapping, else the built-in clip; an action
    with none of these is reported and left out); each line becomes a dialogue line with its
    recording and the speaker's expression; the camera becomes the scene camera."""
    fps = m.render.fps
    issues: list[Issue] = []
    frames = max(2, round(shot.duration_s * fps))
    frame = lambda s: min(frames, 1 + round(s * fps))  # noqa: E731
    env = next((e for e in m.environments if e.code == shot.environment), None) if shot.environment else None
    if shot.environment and env is None:
        issues.append(Issue(path="environment", code="not_cast", message=f"the episode pins no version of {shot.environment}"))
    instances: list[CharacterInstance] = []
    focus_key = None
    for j, pl in enumerate(shot.placement):
        member = m.member(pl.character_id)
        if member is None:
            issues.append(Issue(path=f"placement[{j}].character_id", code="not_cast",
                                message=f"{pl.character_id} is not in the episode's cast"))
            continue
        if shot.camera.focus == pl.character_id:
            focus_key = member.key
        anims = []
        for a in (x for x in shot.actions if x.character_id == pl.character_id):
            ref = a.animation or m.action_animations.get(a.action) or ACTION_CLIPS.get(a.action)
            if ref is None:
                issues.append(Issue(path=f"actions.{pl.character_id}.{a.action}", code="no_clip", severity="warning",
                                    message=f"nothing plays {a.action!r}; it is left out of the shot"))
                continue
            anims.append(AnimationUse(clip=ref, start_frame=frame(a.start_s), repeat=a.repeat, travel=a.travel))
        if not anims:
            anims.append(AnimationUse(clip="idle", start_frame=1, repeat=max(1, math.ceil(frames / 48))))
        lines, expr = [], [ExpressionKey(frame=1, expression=pl.emotion or NEUTRAL)]
        for ln in sorted((x for x in shot.dialogue if x.character_id == pl.character_id), key=lambda x: x.start_s):
            start = frame(ln.start_s)
            end = min(frames, max(start + 1, frame(ln.start_s + line_span(ln, audio_s))))
            lines.append(DialogueLine(start_frame=start, end_frame=end, text=ln.text, audio=ln.audio))
            if ln.emotion and ln.emotion != expr[-1].expression and start > expr[-1].frame:
                expr.append(ExpressionKey(frame=start, expression=ln.emotion))
        instances.append(CharacterInstance(
            key=member.key, character=member.character, outfit=member.outfit, accessories=member.accessories,
            mark=pl.mark if env else None, transform=Transform(location=(pl.at[0], pl.at[1], 0.0),
                                                               rotation_deg=(0.0, 0.0, pl.facing_deg)),
            animations=anims, dialogue=lines, expressions=expr, state={"shot": shot.shot_id}))
    cam = SceneCamera(preset=shot.camera.type, mark=shot.camera.mark if env else None, angle_deg=shot.camera.angle_deg,
                      focus=focus_key, movement=shot.camera.movement)
    manifest = SceneManifest(name=name or f"{m.title} {shot.shot_id}", description=shot.description[:4000], fps=fps,
                             frame_start=1, frame_end=frames, instances=instances, camera=cam,
                             environment=env, lighting=shot.lighting) if instances else None
    if manifest is None:
        issues.append(Issue(path="placement", code="empty", message="no cast member is placed in the shot"))
    return manifest, issues
