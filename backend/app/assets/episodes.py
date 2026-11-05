"""Series, episodes, scripts, storyboards and episode versions (SRS 13 to 16, Phase 8).

* A **series** casts characters by identity (``series_members``) with a default character
  and outfit version and a voice, and keeps an asset collection of exact versions
  (``series_assets``). Nothing is copied: scenes link the published files.
* A **script version** is a ``ScriptSpecification`` checked against the series: every
  character it names is a series member, every location an environment with a published
  version. It is never edited; an edit is the next number.
* A **storyboard** is a ``StoryboardSpecification`` for one script version: by rule
  (``plan``), from the AI (a suggestion only), by hand, or **voiced** (the same shots with
  each line's recording and the shot lengthened to fit it).
* An **episode version** compiles a storyboard with an exact cast into one scene version per
  shot (``shot_to_scene`` then the Phase 5 to 7 scene resolution, so every pin is checked
  the same way a hand-made scene is). A shot whose manifest did not change reuses the scene
  version it had, so a new episode version only rebuilds what changed. The version records
  the closure of every library version and audio clip it depends on; once **locked** none of
  it changes, and only a locked version can be rendered.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from studio_contracts.character import Issue
from studio_contracts.episode import (
    CastMember,
    EpisodeManifest,
    EpisodeShot,
    ScriptSpecification,
    SeriesSettings,
    StoryboardSpecification,
    VoiceProfile,
    line_span,
    plan_storyboard,
    shot_to_scene,
    validate_episode,
    validate_script,
    validate_storyboard,
)
from studio_contracts.ids import uuid7
from studio_contracts.scene import Pin, ResolvedScene

from app.assets import scenes as scene_svc
from app.core.errors import ApiError
from app.models.assets import AssetVersion
from app.models.audio import AudioClip
from app.models.characters import Character, CharacterVersion
from app.models.episodes import (
    Episode,
    EpisodeAsset,
    EpisodeAudio,
    EpisodeScene,
    EpisodeVersion,
    EpisodeVersionShot,
    Script,
    ScriptVersion,
    Series,
    SeriesAsset,
    SeriesMember,
    ShotRow,
    Storyboard,
)
from app.models.outfits import OutfitVersion
from app.models.scenes import Scene, SceneVersion
from app.models.sets import EnvironmentVersion

USABLE = ("published", "deprecated")


def now() -> datetime:
    return datetime.now(UTC)


def _code(session: Session, kind: str, prefix: str) -> str:
    n = session.execute(text(
        "INSERT INTO asset_code_counters (kind, next_number) VALUES (:k, 2) "
        "ON CONFLICT (kind) DO UPDATE SET next_number = asset_code_counters.next_number + 1 "
        "RETURNING next_number - 1"), {"k": kind}).scalar_one()
    return f"{prefix}-{n:03d}"


def _issues_error(code: str, message: str, issues: list[Issue], status: int = 422) -> ApiError:
    return ApiError(status, code, message, issues=[i.model_dump() for i in issues])


def _parse(model, raw: dict[str, Any], what: str):
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        issues = [{"path": ".".join(str(p) for p in e["loc"]) or "$", "code": e["type"], "message": e["msg"]}
                  for e in exc.errors(include_url=False, include_context=False)]
        raise ApiError(422, f"invalid_{what}", f"the {what} is not valid", issues=issues) from None


def _by_ref(session: Session, model, ref: str, label: str):
    """A row by its id or its code."""
    try:
        row = session.get(model, uuid.UUID(ref))
    except ValueError:
        row = session.scalar(select(model).where(model.code == ref))
    if row is None:
        raise ApiError(404, f"{label}_not_found", ref)
    return row


# ---------------------------------------------------------------- versions in the registry
def version_by_pin(session: Session, kind: str, pin: Pin):
    return scene_svc.find_version(session, kind, pin)


def latest_usable(session: Session, kind: str, code: str):
    """The recommended version when there is one, else the newest published version."""
    model = scene_svc.MODELS[kind]
    rows = list(session.scalars(select(model).join(AssetVersion, AssetVersion.id == model.id).where(
        AssetVersion.kind == kind, AssetVersion.display_code == code, AssetVersion.status == "published")
        .order_by(AssetVersion.major.desc(), AssetVersion.minor.desc())))
    if not rows:
        return None
    owner = rows[0].character if kind == "character" else getattr(rows[0], "owner", None)
    rec = getattr(owner, "recommended_version_id", None)
    return next((r for r in rows if r.id == rec), rows[0])


def pin_of(v) -> Pin:
    return Pin(code=v.entry.display_code, version=v.entry.version_label)


# ---------------------------------------------------------------- series
@dataclass
class MemberIn:
    character: str  # code or id
    key: str
    role: str = "main"
    character_version: Pin | None = None
    outfit_version: Pin | None = None
    voice: dict[str, Any] = field(default_factory=dict)


def get_series(session: Session, ref: str) -> Series:
    return _by_ref(session, Series, ref, "series")


def create_series(session: Session, *, name: str, description: str, settings: dict[str, Any],
                  members: list[MemberIn], assets: list[uuid.UUID], created_by: uuid.UUID | None) -> Series:
    s = _parse(SeriesSettings, settings, "series_settings")
    series = Series(id=uuid7(), code=_code(session, "series", "SERIES"), name=name, description=description,
                    style_profile_ref=f"{s.style_profile.id}@{s.style_profile.version}",
                    settings=s.model_dump(mode="json"), created_by=created_by)
    session.add(series)
    session.flush()
    set_members(session, series, members)
    for a in assets:
        add_asset(session, series, a)
    session.flush()
    session.refresh(series)
    return series


def set_members(session: Session, series: Series, members: list[MemberIn]) -> None:
    """Replace the cast list. A member is a character identity; the default versions must be
    published versions of it, and the outfit's fit on the default character version is
    checked when an episode version is made (it may be approved later)."""
    issues: list[Issue] = []
    rows = []
    keys, chars = set(), set()
    for i, m in enumerate(members):
        p = f"members[{i}]"
        try:
            ch = _by_ref(session, Character, m.character, "character")
        except ApiError:
            issues.append(Issue(path=f"{p}.character", code="not_found", message=f"no character {m.character}"))
            continue
        if m.key in keys or ch.id in chars:
            issues.append(Issue(path=p, code="duplicate", message="a character or key is in the series twice"))
        keys.add(m.key)
        chars.add(ch.id)
        cv = ov = None
        if m.character_version is not None:
            if m.character_version.code != ch.code:
                issues.append(Issue(path=f"{p}.character_version", code="wrong_character",
                                    message=f"{m.character_version.ref} is not a version of {ch.code}"))
            else:
                cv = version_by_pin(session, "character", m.character_version)
                if cv is None or cv.status not in USABLE:
                    issues.append(Issue(path=f"{p}.character_version", code="not_published",
                                        message=f"{m.character_version.ref} is not a published version"))
        elif latest_usable(session, "character", ch.code) is None:
            issues.append(Issue(path=f"{p}.character", code="not_published",
                                message=f"{ch.code} has no published version to cast"))
        if m.outfit_version is not None:
            ov = version_by_pin(session, "outfit", m.outfit_version)
            if ov is None or ov.status not in USABLE:
                issues.append(Issue(path=f"{p}.outfit_version", code="not_published",
                                    message=f"{m.outfit_version.ref} is not a published outfit version"))
        try:
            voice = VoiceProfile.model_validate(m.voice or {})
        except ValidationError as exc:
            issues.append(Issue(path=f"{p}.voice", code="invalid", message=str(exc.errors()[0]["msg"])))
            continue
        rows.append(SeriesMember(id=uuid7(), series_id=series.id, character_id=ch.id, key=m.key, role=m.role,
                                 position=i, default_character_version_id=cv.id if cv else None,
                                 default_outfit_version_id=ov.id if ov else None, voice=voice.model_dump(mode="json")))
    if issues:
        raise _issues_error("invalid_members", "the series cast cannot be saved", issues)
    for old in list(series.members):
        session.delete(old)
    session.flush()
    for r in rows:
        session.add(r)
    session.flush()
    session.refresh(series)


def add_asset(session: Session, series: Series, version_id: uuid.UUID) -> SeriesAsset:
    v = session.get(AssetVersion, version_id)
    if v is None or v.status not in USABLE:
        raise ApiError(422, "not_published", f"{version_id} is not a published library version")
    existing = next((a for a in series.assets if a.asset_version_id == v.id), None)
    if existing:
        return existing
    row = SeriesAsset(id=uuid7(), series_id=series.id, asset_version_id=v.id, kind=v.kind)
    series.assets.append(row)
    session.flush()
    return row


def member_codes(session: Session, series: Series) -> dict[str, SeriesMember]:
    out = {}
    for m in series.members:
        ch = session.get(Character, m.character_id)
        out[ch.code] = m
    return out


def environment_codes(session: Session) -> set[str]:
    return set(session.scalars(select(AssetVersion.display_code).where(
        AssetVersion.kind == "environment", AssetVersion.status == "published")))


# ---------------------------------------------------------------- episodes
def get_episode(session: Session, ref: str) -> Episode:
    return _by_ref(session, Episode, ref, "episode")


def create_episode(session: Session, series: Series, *, title: str, concept: str, number: int | None,
                   created_by: uuid.UUID | None) -> Episode:
    taken = set(session.scalars(select(Episode.number).where(Episode.series_id == series.id)))
    n = number or 1 + max(taken, default=0)
    if n in taken:
        raise ApiError(409, "episode_number_taken", f"{series.code} already has episode {n}")
    ep = Episode(id=uuid7(), code=_code(session, "episode", "EP"), series_id=series.id, number=n, title=title,
                 concept=concept, created_by=created_by)
    session.add(ep)
    session.flush()
    session.add(Script(id=uuid7(), episode_id=ep.id))
    session.flush()
    return ep


def script_of(session: Session, ep: Episode) -> Script:
    return session.scalar(select(Script).where(Script.episode_id == ep.id))


def script_versions(session: Session, ep: Episode) -> list[ScriptVersion]:
    sc = script_of(session, ep)
    return list(session.scalars(select(ScriptVersion).where(ScriptVersion.script_id == sc.id)
                                .order_by(ScriptVersion.number)))


def get_script_version(session: Session, ep: Episode, ref: str | uuid.UUID | int) -> ScriptVersion:
    for v in script_versions(session, ep):
        if str(v.id) == str(ref) or (isinstance(ref, int) and v.number == ref):
            return v
    raise ApiError(404, "script_version_not_found", str(ref))


def check_script(session: Session, ep: Episode, spec: ScriptSpecification) -> list[Issue]:
    series = session.get(Series, ep.series_id)
    issues = validate_script(spec, set(member_codes(session, series)), environment_codes(session))
    return [i.model_copy(update={"message": f"{i.message}; cast it in {series.code} first"})
            if i.code == "not_found" and "character" in i.path else i for i in issues]


def add_script_version(session: Session, ep: Episode, raw: dict[str, Any], *, source: str = "manual",
                       ai_generation_id: uuid.UUID | None = None, based_on: ScriptVersion | None = None,
                       notes: str = "", created_by: uuid.UUID | None) -> tuple[ScriptVersion, list[Issue]]:
    spec = _parse(ScriptSpecification, {**raw, "episode_id": ep.code}, "script")
    issues = check_script(session, ep, spec)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise _issues_error("invalid_script", "the script refers to something the series does not have", issues)
    sc = script_of(session, ep)
    number = 1 + max((v.number for v in script_versions(session, ep)), default=0)
    v = ScriptVersion(id=uuid7(), script_id=sc.id, number=number, spec=spec.model_dump(mode="json"),
                      digest=spec.digest(), source=source, ai_generation_id=ai_generation_id,
                      based_on_id=based_on.id if based_on else None, notes=notes, created_by=created_by)
    session.add(v)
    session.flush()
    return v, issues


# ---------------------------------------------------------------- storyboards
def storyboards(session: Session, ep: Episode) -> list[Storyboard]:
    return list(session.scalars(select(Storyboard).where(Storyboard.episode_id == ep.id).order_by(Storyboard.number)))


def get_storyboard(session: Session, ref: str | uuid.UUID) -> Storyboard:
    try:
        sb = session.get(Storyboard, uuid.UUID(str(ref)))
    except ValueError:
        sb = None
    if sb is None:
        raise ApiError(404, "storyboard_not_found", str(ref))
    return sb


def storyboard_spec(sb: Storyboard) -> StoryboardSpecification:
    return StoryboardSpecification(shots=[s.spec for s in sb.shots])


def environment_layouts(session: Session, codes: set[str], series: Series | None = None) -> dict[str, dict]:
    """The layout each environment code will be used with: the series' pinned version when
    it has one, else the recommended or newest published version."""
    out: dict[str, dict] = {}
    pinned = {}
    if series is not None:
        for a in series.assets:
            if a.kind == "environment":
                ev = session.get(EnvironmentVersion, a.asset_version_id)
                pinned[ev.entry.display_code] = ev
    for code in codes:
        ev = pinned.get(code) or latest_usable(session, "environment", code)
        if ev is not None and ev.layout:
            out[code] = ev.layout
    return out


def _fit_cameras(sb: StoryboardSpecification, layouts: dict[str, dict]) -> StoryboardSpecification:
    """The rule plan frames establishing shots from a ``wide`` camera mark; an environment
    without one uses its first camera mark, or the framed preset."""
    shots = []
    for s in sb.shots:
        if s.camera.mark and s.environment:
            cams = [c["name"] for c in layouts.get(s.environment, {}).get("cameras", [])]
            if s.camera.mark not in cams:
                s = s.model_copy(update={"camera": s.camera.model_copy(update={"mark": cams[0] if cams else None})})
        shots.append(s)
    return StoryboardSpecification(shots=shots)


def spawn_marks(layouts: dict[str, dict]) -> dict[str, list[str]]:
    return {code: [m["name"] for m in lay.get("marks", []) if m.get("kind", "spawn") == "spawn"]
            for code, lay in layouts.items()}


def plan(session: Session, ep: Episode, sv: ScriptVersion) -> StoryboardSpecification:
    script = ScriptSpecification.model_validate(sv.spec)
    layouts = environment_layouts(session, set(script.locations()), session.get(Series, ep.series_id))
    return _fit_cameras(plan_storyboard(script, marks=spawn_marks(layouts)), layouts)


def audio_lengths(session: Session, codes: set[str]) -> tuple[dict[str, float], list[Issue]]:
    lengths, issues = {}, []
    for code in sorted(codes):
        clip = session.scalar(select(AudioClip).where(AudioClip.code == code))
        if clip is None:
            issues.append(Issue(path=code, code="not_found", message=f"no audio clip {code}"))
        elif clip.status != "ready":
            issues.append(Issue(path=code, code="not_ready", message=f"{code} is {clip.status}"))
        else:
            lengths[code] = clip.duration_s
    return lengths, issues


def line_audio(sb: StoryboardSpecification) -> set[str]:
    return {ln.audio for s in sb.shots for ln in s.dialogue if ln.audio}


def add_storyboard(session: Session, ep: Episode, sv: ScriptVersion, spec: dict[str, Any] | StoryboardSpecification,
                   *, source: str, ai_generation_id: uuid.UUID | None = None, based_on: Storyboard | None = None,
                   notes: str = "", created_by: uuid.UUID | None) -> tuple[Storyboard, list[Issue]]:
    sb = spec if isinstance(spec, StoryboardSpecification) else _parse(StoryboardSpecification, spec, "storyboard")
    script = ScriptSpecification.model_validate(sv.spec)
    lengths, audio_issues = audio_lengths(session, line_audio(sb))
    issues = [*audio_issues, *validate_storyboard(sb, script, lengths)]
    if any(i.severity == "error" for i in issues):
        raise _issues_error("invalid_storyboard", "the storyboard cannot be saved", issues)
    number = 1 + max((s.number for s in storyboards(session, ep)), default=0)
    row = Storyboard(id=uuid7(), episode_id=ep.id, number=number, script_version_id=sv.id, source=source,
                     ai_generation_id=ai_generation_id, based_on_id=based_on.id if based_on else None,
                     digest=sb.digest(), duration_s=round(sb.duration_s(), 3), notes=notes, created_by=created_by)
    session.add(row)
    session.flush()
    for pos, s in enumerate(sb.shots):
        session.add(ShotRow(id=uuid7(), storyboard_id=row.id, position=pos, shot_id=s.shot_id, scene_id=s.scene_id,
                            duration_s=s.duration_s, camera_type=s.camera.type, spec=s.model_dump(mode="json")))
    session.flush()
    session.refresh(row)
    return row, issues


# ---------------------------------------------------------------- voices
def voice_for(session: Session, ep: Episode, character_code: str) -> VoiceProfile:
    member = member_codes(session, session.get(Series, ep.series_id)).get(character_code)
    return VoiceProfile.model_validate(member.voice if member else {})


def find_tts_clip(session: Session, text_: str, voice: VoiceProfile) -> AudioClip | None:
    """A clip already spoken with exactly this text and voice (ready or in progress), so a
    line keeps its recording across storyboard versions."""
    want = voice.model_dump(mode="json")
    for clip in session.scalars(select(AudioClip).where(AudioClip.source == "tts", AudioClip.dialog_text == text_,
                                                        AudioClip.status.in_(("ready", "analysing")))
                                .order_by(AudioClip.created_at.desc())):
        if clip.voice == want:
            return clip
    return None


def create_tts_clip(session: Session, *, name: str, text_: str, voice: VoiceProfile,
                    created_by: uuid.UUID | None) -> AudioClip:
    from app.assets import audio as audio_svc

    clip = AudioClip(code=audio_svc.allocate_code(session), name=name[:120], kind="dialogue", source="tts",
                     voice=voice.model_dump(mode="json"), dialog_text=text_, status="analysing", created_by=created_by)
    session.add(clip)
    session.flush()
    return clip


@dataclass
class VoicePlan:
    lines: list[tuple[int, int, AudioClip, bool]]  # (shot index, line index, clip, new)


def plan_voices(session: Session, ep: Episode, sb: Storyboard, created_by: uuid.UUID | None) -> VoicePlan:
    """A clip for every line without a recording: an existing one with the same text and
    voice, or a new speech clip (its synthesis job is submitted by the caller)."""
    spec = storyboard_spec(sb)
    out = []
    for i, s in enumerate(spec.shots):
        for j, ln in enumerate(s.dialogue):
            if ln.audio:
                continue
            voice = voice_for(session, ep, ln.character_id)
            clip = find_tts_clip(session, ln.text, voice)
            new = clip is None
            if new:
                clip = create_tts_clip(session, name=f"{ep.code} {s.shot_id} {ln.character_id}", text_=ln.text,
                                       voice=voice, created_by=created_by)
            out.append((i, j, clip, new))
    return VoicePlan(out)


def voiced(session: Session, ep: Episode, sb: Storyboard, created_by: uuid.UUID | None,
           pause_s: float = 0.4) -> tuple[Storyboard, list[Issue]]:
    """The storyboard with each line's recording: lines take their ready speech clip, and a
    shot is lengthened when its recording runs past the end (plus a short pause)."""
    spec = storyboard_spec(sb)
    issues: list[Issue] = []
    shots = []
    for i, s in enumerate(spec.shots):
        lines = []
        for j, ln in enumerate(s.dialogue):
            if ln.audio is None:
                clip = find_tts_clip(session, ln.text, voice_for(session, ep, ln.character_id))
                if clip is None or clip.status != "ready":
                    issues.append(Issue(path=f"shots[{i}].dialogue[{j}]", code="not_voiced",
                                        message=f"{s.shot_id}'s line has no ready recording"
                                                + (f" ({clip.code} is {clip.status})" if clip else "")))
                    lines.append(ln)
                    continue
                ln = ln.model_copy(update={"audio": clip.code})
            lines.append(ln)
        lengths, _ = audio_lengths(session, {ln.audio for ln in lines if ln.audio})
        need = max((ln.start_s + line_span(ln, lengths) for ln in lines), default=0.0) + pause_s
        dur = round(min(30.0, max(s.duration_s, need)), 2)
        shots.append(s.model_copy(update={"dialogue": lines, "duration_s": dur}))
    if issues:
        raise _issues_error("not_voiced", "some lines have no ready recording yet", issues, status=409)
    sv = session.get(ScriptVersion, sb.script_version_id)
    return add_storyboard(session, ep, sv, StoryboardSpecification(shots=shots), source="voiced", based_on=sb,
                          notes=f"voiced from storyboard {sb.number}", created_by=created_by)


# ---------------------------------------------------------------- episode versions
@dataclass
class CastIn:
    character: str  # CHAR code
    key: str | None = None
    character_version: Pin | None = None
    outfit: Pin | None = None
    accessories: list[dict[str, Any]] = field(default_factory=list)
    voice: dict[str, Any] | None = None


def default_cast(session: Session, ep: Episode, script: ScriptSpecification,
                 overrides: list[CastIn]) -> tuple[list[CastMember], list[Issue]]:
    """The cast for the script's characters: each from its override, else the series
    member's defaults (character version: the default, else the recommended or newest
    published one)."""
    series = session.get(Series, ep.series_id)
    members = member_codes(session, series)
    by_code = {o.character: o for o in overrides}
    cast, issues = [], []
    for code in script.characters():
        m, o = members.get(code), by_code.get(code)
        if m is None:
            issues.append(Issue(path=f"cast.{code}", code="not_in_series", message=f"{code} is not cast in {series.code}"))
            continue
        cv = session.get(CharacterVersion, m.default_character_version_id) if m.default_character_version_id else None
        cpin = (o.character_version if o and o.character_version else None) or (pin_of(cv) if cv else None)
        if cpin is None:
            latest = latest_usable(session, "character", code)
            if latest is None:
                issues.append(Issue(path=f"cast.{code}", code="not_published", message=f"{code} has no published version"))
                continue
            cpin = pin_of(latest)
        ov = session.get(OutfitVersion, m.default_outfit_version_id) if m.default_outfit_version_id else None
        opin = (o.outfit if o and o.outfit else None) or (pin_of(ov) if ov else None)
        voice = VoiceProfile.model_validate((o.voice if o and o.voice is not None else None) or m.voice or {})
        try:
            cast.append(CastMember(key=(o.key if o and o.key else m.key), character=cpin, outfit=opin,
                                   accessories=(o.accessories if o else []), voice=voice))
        except ValidationError as exc:
            issues.append(Issue(path=f"cast.{code}", code="invalid", message=str(exc.errors()[0]["msg"])))
    for code in by_code:
        if code not in script.characters():
            issues.append(Issue(path=f"cast.{code}", code="not_in_script", severity="warning",
                                message=f"{code} is not in the script; ignored"))
    return cast, issues


def default_environments(session: Session, ep: Episode, sb: StoryboardSpecification,
                         overrides: list[Pin]) -> tuple[list[Pin], list[Issue]]:
    series = session.get(Series, ep.series_id)
    pinned = {}
    for a in series.assets:
        if a.kind == "environment":
            ev = session.get(EnvironmentVersion, a.asset_version_id)
            pinned[ev.entry.display_code] = pin_of(ev)
    given = {p.code: p for p in overrides}
    out, issues = [], []
    for code in sorted({s.environment for s in sb.shots if s.environment}):
        p = given.get(code) or pinned.get(code)
        if p is None:
            ev = latest_usable(session, "environment", code)
            if ev is None:
                issues.append(Issue(path=f"environments.{code}", code="not_published",
                                    message=f"{code} has no published version"))
                continue
            p = pin_of(ev)
        out.append(p)
    return out, issues


def episode_scene(session: Session, ep: Episode, shot_id: str, name: str) -> Scene | None:
    row = session.scalar(select(EpisodeScene).where(EpisodeScene.episode_id == ep.id, EpisodeScene.shot_id == shot_id))
    return scene_svc.get_scene(session, row.scene_id, lock=True) if row else None


def versions(session: Session, ep: Episode) -> list[EpisodeVersion]:
    return list(session.scalars(select(EpisodeVersion).where(EpisodeVersion.episode_id == ep.id)
                                .order_by(EpisodeVersion.number)))


def get_version(session: Session, ref: str | uuid.UUID, *, lock: bool = False) -> EpisodeVersion:
    try:
        vid = uuid.UUID(str(ref))
    except ValueError:
        raise ApiError(404, "episode_version_not_found", str(ref)) from None
    v = session.get(EpisodeVersion, vid, with_for_update=lock or None, populate_existing=lock)
    if v is None:
        raise ApiError(404, "episode_version_not_found", str(ref))
    return v


@dataclass
class Compiled:
    manifest: EpisodeManifest
    shots: list[tuple[str, SceneVersion, float, float, dict[str, Any], bool]]  # id, version, start, dur, transition, new
    issues: list[Issue]


def compile_version(session: Session, ep: Episode, sb: Storyboard, *, cast: list[CastIn], environments: list[Pin],
                    extra: dict[str, Any], allow_verified_fits: bool, created_by: uuid.UUID | None) -> Compiled:
    """Every shot to a scene version (new, or the one it already had when nothing changed).
    All problems of all shots are reported together; nothing is written unless all pass."""
    series = session.get(Series, ep.series_id)
    sv = session.get(ScriptVersion, sb.script_version_id)
    script = ScriptSpecification.model_validate(sv.spec)
    spec = storyboard_spec(sb)
    issues: list[Issue] = []
    members, cast_issues = default_cast(session, ep, script, cast)
    envs, env_issues = default_environments(session, ep, spec, environments)
    issues += cast_issues + env_issues
    render = {**SeriesSettings.model_validate(series.settings).render.model_dump(mode="json"),
              **(extra.get("render") or {})}
    raw = {"title": ep.title, "script_version": sv.number, "storyboard": sb.number,
           "cast": [c.model_dump(mode="json") for c in members], "environments": [e.model_dump() for e in envs],
           **{k: v for k, v in extra.items() if k != "render"}, "render": render}
    if not members:
        raise _issues_error("invalid_episode", "the episode has no cast", issues)
    m = _parse(EpisodeManifest, raw, "episode")
    issues += validate_episode(m)
    cue_codes = {c.audio for c in [*m.music, *m.sfx]}
    lengths, a_issues = audio_lengths(session, line_audio(spec) | cue_codes)
    issues += a_issues
    for c in [*m.music, *m.sfx]:
        clip = session.scalar(select(AudioClip).where(AudioClip.code == c.audio))
        want = "music" if c in m.music else "sfx"
        if clip is not None and clip.kind != want:
            issues.append(Issue(path=f"{want}.{c.audio}", code="wrong_kind", message=f"{c.audio} is {clip.kind}"))
    resolutions = []
    for i, shot in enumerate(spec.shots):
        sm, sh_issues = shot_to_scene(shot, m, audio_s=lengths, name=f"{ep.code} {shot.shot_id}")
        issues += [i_.model_copy(update={"path": f"shots[{i}].{i_.path}"}) for i_ in sh_issues]
        if sm is None:
            continue
        scene = episode_scene(session, ep, shot.shot_id, sm.name)
        latest = scene.versions[-1] if scene and scene.versions else None
        if latest is not None and latest.manifest_digest == sm.digest():
            resolutions.append((shot, scene, latest, None))
            continue
        res = scene_svc.resolve(session, sm, base=latest, allow_verified_fits=allow_verified_fits)
        issues += [i_.model_copy(update={"path": f"shots[{i}].scene.{i_.path}"}) for i_ in res.issues]
        resolutions.append((shot, scene, latest, res))
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise _issues_error("invalid_episode", f"{len(errors)} problem(s) stop this episode version", issues)
    shots, start, prev = [], 0.0, None
    eshots = []
    for shot, scene, latest, res in resolutions:
        new = res is not None
        if new:
            if scene is None:
                scene = Scene(id=uuid7(), code=scene_svc.allocate_code(session), name=res.manifest.name,
                              description=f"{ep.code} {shot.shot_id}: {shot.description}"[:4000],
                              tags=["episode", ep.code.lower()], created_by=created_by)
                session.add(scene)
                session.flush()
                session.add(EpisodeScene(id=uuid7(), episode_id=ep.id, shot_id=shot.shot_id, scene_id=scene.id))
                session.flush()
            version = scene_svc._insert_version(session, scene, res, notes=f"{ep.code} storyboard {sb.number}",
                                                created_by=created_by, base=latest)
        else:
            version = latest
        t = shot.transition_in
        if prev is not None and t.kind == "dissolve":
            start -= min(t.duration_s, prev, shot.duration_s)
        shots.append((shot.shot_id, version, round(start, 4), shot.duration_s, t.model_dump(mode="json"), new))
        eshots.append(EpisodeShot(shot_id=shot.shot_id, scene=scene.code, version=version.number, transition_in=t))
        start += shot.duration_s
        prev = shot.duration_s
    m = m.model_copy(update={"shots": eshots})
    return Compiled(manifest=m, shots=shots, issues=issues)


def create_version(session: Session, ep: Episode, sb: Storyboard, *, cast: list[CastIn], environments: list[Pin],
                   extra: dict[str, Any], allow_verified_fits: bool, notes: str,
                   created_by: uuid.UUID | None) -> tuple[EpisodeVersion, Compiled]:
    if sb.episode_id != ep.id:
        raise ApiError(422, "wrong_episode", "the storyboard belongs to another episode")
    c = compile_version(session, ep, sb, cast=cast, environments=environments, extra=extra,
                        allow_verified_fits=allow_verified_fits, created_by=created_by)
    number = 1 + max((v.number for v in versions(session, ep)), default=0)
    end = max((s[2] + s[3] for s in c.shots), default=0.0)
    v = EpisodeVersion(id=uuid7(), episode_id=ep.id, number=number, status="draft",
                       manifest=c.manifest.model_dump(mode="json"), digest=c.manifest.digest(),
                       script_version_id=sb.script_version_id, storyboard_id=sb.id, duration_s=round(end, 3),
                       notes=notes, created_by=created_by)
    session.add(v)
    session.flush()
    closure: dict[uuid.UUID, tuple[str, str]] = {}
    audio: set[tuple[str, str]] = set()
    for pos, (shot_id, sv, start, dur, tr, _) in enumerate(c.shots):
        session.add(EpisodeVersionShot(id=uuid7(), episode_version_id=v.id, position=pos, shot_id=shot_id,
                                       scene_version_id=sv.id, start_s=start, duration_s=dur, transition=tr))
        rs = ResolvedScene.model_validate(sv.resolved)
        for p in rs.pins():
            closure[uuid.UUID(p.version_id)] = (p.kind, f"{p.code}@{p.version}")
        for a in rs.audio():
            audio.add((a.code, "dialogue"))
    for cue in c.manifest.music:
        audio.add((cue.audio, "music"))
    for cue in c.manifest.sfx:
        audio.add((cue.audio, "sfx"))
    for vid, (kind, display) in sorted(closure.items(), key=lambda kv: kv[1][1]):
        session.add(EpisodeAsset(episode_version_id=v.id, asset_version_id=vid, kind=kind, display=display))
    for code, role in sorted(audio):
        clip = session.scalar(select(AudioClip).where(AudioClip.code == code))
        session.add(EpisodeAudio(episode_version_id=v.id, audio_clip_id=clip.id, role=role))
    session.flush()
    session.refresh(v)
    return v, c


def closure(session: Session, v: EpisodeVersion) -> list[EpisodeAsset]:
    return list(session.scalars(select(EpisodeAsset).where(EpisodeAsset.episode_version_id == v.id)
                                .order_by(EpisodeAsset.display)))


def audio_of(session: Session, v: EpisodeVersion) -> list[tuple[EpisodeAudio, AudioClip]]:
    return list(session.execute(select(EpisodeAudio, AudioClip).join(AudioClip, AudioClip.id == EpisodeAudio.audio_clip_id)
                                .where(EpisodeAudio.episode_version_id == v.id).order_by(AudioClip.code)).all())


def shot_versions(session: Session, v: EpisodeVersion) -> list[tuple[EpisodeVersionShot, SceneVersion]]:
    return [(s, session.get(SceneVersion, s.scene_version_id)) for s in v.shots]


def progress(session: Session, v: EpisodeVersion) -> dict[str, Any]:
    """How far the shots' builds and reopen checks are."""
    build: dict[str, int] = {}
    verify: dict[str, int] = {}
    for _, sv in shot_versions(session, v):
        b, r = scene_svc.build_status(session, sv), scene_svc.verify_status(session, sv)
        build[b] = build.get(b, 0) + 1
        verify[r] = verify.get(r, 0) + 1
    n = len(v.shots)
    return {"shots": n, "build": build, "verify": verify, "built": build.get("built", 0) == n,
            "verified": verify.get("verified", 0) == n}


def lock(session: Session, v: EpisodeVersion, who: uuid.UUID | None) -> None:
    if v.status == "locked":
        raise ApiError(409, "already_locked", "this episode version is locked")
    p = progress(session, v)
    if not p["built"]:
        raise ApiError(409, "not_built", "every shot's scene must be built before the version is locked", progress=p)
    for _, clip in audio_of(session, v):
        if clip.status != "ready":
            raise ApiError(409, "audio_not_ready", f"{clip.code} is {clip.status}")
    for _, sv in shot_versions(session, v):
        scene_svc.check_pins_still_usable(session, sv)
    v.status, v.locked_at, v.locked_by = "locked", now(), who
    session.flush()
