"""Series, episodes, scripts, storyboards, episode versions and render jobs (SRS 13 to 18,
23; Phase 8 Episode Production).

The flow: a series casts published characters -> an episode gets a script version (written,
or suggested by the AI and kept by a person) -> a storyboard (by rule, AI suggestion or by
hand) -> its lines are voiced (speech synthesis on the media queue) into a voiced storyboard
-> an episode version compiles the shots into scene versions with exact pins -> build every
shot (Blender jobs) -> lock -> render (one Blender job per shot, then an FFmpeg export).
The AI never saves or publishes anything by itself: its output is a suggestion a person
saves as a version.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from studio_contracts.episode import AudioCue, VoiceProfile
from studio_contracts.jobs import ScenePreviewSettings
from studio_contracts.scene import AccessoryUse, Pin

from app.api.v1.jobs import _out as job_out
from app.api.v1.jobs import submit_and_dispatch
from app.api.v1.schemas import DownloadOut, JobOut, Page
from app.assets import episodes as svc
from app.assets import renders as rsvc
from app.assets import scenes as scene_svc
from app.core.auth import Principal, audit, require
from app.core.database import get_session
from app.core.errors import ApiError
from app.core.storage import Storage
from app.jobs import service
from app.jobs.queue import get_dispatcher
from app.models.characters import Character
from app.models.episodes import Episode, EpisodeScene, EpisodeVersion, RenderJob, Series
from app.models.jobs import Job
from app.models.scenes import Scene

series_router = APIRouter(prefix="/series", tags=["series"])
episodes_router = APIRouter(prefix="/episodes", tags=["episodes"])
storyboards_router = APIRouter(prefix="/storyboards", tags=["episodes"])
versions_router = APIRouter(prefix="/episode-versions", tags=["episodes"])
renders_router = APIRouter(prefix="/render-jobs", tags=["rendering"])


def _dispatch(session: Session, jobs: list[Job]) -> None:
    """After the commit that made them: hand each job to the broker."""
    for j in jobs:
        get_dispatcher().send(j.id, j.queue)
        service.mark_enqueued(session, j.id)
    session.commit()


def _need(who: Principal, job_type: str) -> None:
    for p in service.extra_permissions(job_type):
        if not who.can(p):
            raise ApiError(403, "forbidden", f"{job_type} jobs need {p}", required=p)


# ================================================================ series
class MemberBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character: str = Field(description="CHAR-001 or the character's id")
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$", description="instance key in every scene, e.g. ali")
    role: Literal["main", "supporting"] = "main"
    character_version: Pin | None = Field(default=None, description="default version; else recommended / newest")
    outfit_version: Pin | None = None
    voice: VoiceProfile = Field(default_factory=VoiceProfile)


class SeriesCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    settings: dict[str, Any] = Field(default_factory=dict, description="SeriesSettings: style profile, render")
    members: list[MemberBody] = Field(default_factory=list, max_length=40)
    assets: list[uuid.UUID] = Field(default_factory=list, max_length=200,
                                    description="asset collection: exact published version ids")


class SeriesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    settings: dict[str, Any] | None = None


class MemberOut(BaseModel):
    id: uuid.UUID
    character_id: uuid.UUID
    character_code: str
    character_name: str
    key: str
    role: str
    default_character_version_id: uuid.UUID | None
    default_outfit_version_id: uuid.UUID | None
    voice: dict[str, Any]


class SeriesAssetOut(BaseModel):
    asset_version_id: uuid.UUID
    kind: str
    display: str


class SeriesOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str
    style_profile_ref: str
    settings: dict[str, Any]
    members: list[MemberOut]
    assets: list[SeriesAssetOut]
    episodes: int
    created_at: datetime
    updated_at: datetime


def _series(session: Session, s: Series) -> SeriesOut:
    members = []
    for m in s.members:
        ch = session.get(Character, m.character_id)
        members.append(MemberOut(id=m.id, character_id=ch.id, character_code=ch.code, character_name=ch.name, key=m.key,
                                 role=m.role, default_character_version_id=m.default_character_version_id,
                                 default_outfit_version_id=m.default_outfit_version_id, voice=m.voice))
    from app.models.assets import AssetVersion

    assets = []
    for a in s.assets:
        v = session.get(AssetVersion, a.asset_version_id)
        assets.append(SeriesAssetOut(asset_version_id=v.id, kind=v.kind, display=f"{v.display_code}@{v.version_label}"))
    n = len(list(session.scalars(select(Episode.id).where(Episode.series_id == s.id))))
    return SeriesOut(id=s.id, code=s.code, name=s.name, description=s.description, style_profile_ref=s.style_profile_ref,
                     settings=s.settings, members=members, assets=assets, episodes=n, created_at=s.created_at,
                     updated_at=s.updated_at)


def _members(body: list[MemberBody]) -> list[svc.MemberIn]:
    return [svc.MemberIn(character=m.character, key=m.key, role=m.role, character_version=m.character_version,
                         outfit_version=m.outfit_version, voice=m.voice.model_dump(mode="json")) for m in body]


@series_router.post("", status_code=status.HTTP_201_CREATED, response_model=SeriesOut,
                    summary="Create a series: settings, cast and asset collection")
def create_series(body: SeriesCreate, session: Session = Depends(get_session),
                  who: Principal = Depends(require("series.manage"))) -> SeriesOut:
    s = svc.create_series(session, name=body.name, description=body.description, settings=body.settings,
                          members=_members(body.members), assets=body.assets, created_by=who.user_id)
    audit(session, who, "series.created", "series", s.id, code=s.code)
    session.commit()
    return _series(session, svc.get_series(session, str(s.id)))


@series_router.get("", response_model=Page[SeriesOut], summary="Series, newest first")
def list_series(session: Session = Depends(get_session), _: Principal = Depends(require("asset.read"))) -> Page[SeriesOut]:
    return Page(items=[_series(session, s) for s in session.scalars(select(Series).order_by(Series.id.desc()))])


@series_router.get("/{ref}", response_model=SeriesOut)
def get_series(ref: str, session: Session = Depends(get_session),
               _: Principal = Depends(require("asset.read"))) -> SeriesOut:
    return _series(session, svc.get_series(session, ref))


@series_router.patch("/{ref}", response_model=SeriesOut, summary="Name, description, settings")
def patch_series(ref: str, body: SeriesPatch, session: Session = Depends(get_session),
                 who: Principal = Depends(require("series.manage"))) -> SeriesOut:
    from studio_contracts.episode import SeriesSettings

    s = svc.get_series(session, ref)
    if body.name is not None:
        s.name = body.name
    if body.description is not None:
        s.description = body.description
    if body.settings is not None:
        st = svc._parse(SeriesSettings, body.settings, "series_settings")
        s.settings = st.model_dump(mode="json")
        s.style_profile_ref = st.style_profile.ref
    audit(session, who, "series.updated", "series", s.id, fields=sorted(body.model_dump(exclude_unset=True)))
    session.commit()
    return _series(session, svc.get_series(session, ref))


@series_router.put("/{ref}/members", response_model=SeriesOut, summary="Replace the series cast")
def put_members(ref: str, body: list[MemberBody], session: Session = Depends(get_session),
                who: Principal = Depends(require("series.manage"))) -> SeriesOut:
    s = svc.get_series(session, ref)
    svc.set_members(session, s, _members(body))
    audit(session, who, "series.cast_changed", "series", s.id, members=[m.key for m in body])
    session.commit()
    return _series(session, svc.get_series(session, ref))


@series_router.post("/{ref}/assets", response_model=SeriesOut, summary="Add exact versions to the asset collection")
def add_assets(ref: str, body: list[uuid.UUID], session: Session = Depends(get_session),
               who: Principal = Depends(require("series.manage"))) -> SeriesOut:
    s = svc.get_series(session, ref)
    for vid in body:
        svc.add_asset(session, s, vid)
    audit(session, who, "series.assets_added", "series", s.id, versions=[str(v) for v in body])
    session.commit()
    return _series(session, svc.get_series(session, ref))


# ================================================================ episodes
class EpisodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    series_id: str = Field(description="SERIES-001 or the series' id")
    title: str = Field(min_length=1, max_length=120)
    concept: str = Field(default="", max_length=4000)
    number: int | None = Field(default=None, ge=1)


class ScriptVersionOut(BaseModel):
    id: uuid.UUID
    number: int
    source: str
    ai_generation_id: uuid.UUID | None
    based_on_id: uuid.UUID | None
    digest: str
    notes: str
    spec: dict[str, Any]
    created_by: uuid.UUID | None
    created_at: datetime


class StoryboardOut(BaseModel):
    id: uuid.UUID
    number: int
    script_version_id: uuid.UUID
    source: str
    ai_generation_id: uuid.UUID | None
    based_on_id: uuid.UUID | None
    digest: str
    duration_s: float
    notes: str
    shots: list[dict[str, Any]]
    created_at: datetime


class EpisodeVersionSummary(BaseModel):
    id: uuid.UUID
    number: int
    status: str
    digest: str
    script_version_id: uuid.UUID
    storyboard_id: uuid.UUID
    duration_s: float
    notes: str
    created_at: datetime
    locked_at: datetime | None


class EpisodeOut(BaseModel):
    id: uuid.UUID
    code: str
    series_id: uuid.UUID
    series_code: str
    number: int
    title: str
    concept: str
    created_at: datetime
    script_versions: list[ScriptVersionOut] = Field(default_factory=list)
    storyboards: list[StoryboardOut] = Field(default_factory=list)
    versions: list[EpisodeVersionSummary] = Field(default_factory=list)


def _sv(v) -> ScriptVersionOut:
    return ScriptVersionOut(**{k: getattr(v, k) for k in ScriptVersionOut.model_fields})


def _sb(sb) -> StoryboardOut:
    return StoryboardOut(**{k: getattr(sb, k) for k in StoryboardOut.model_fields if k != "shots"},
                         shots=[s.spec for s in sb.shots])


def _evs(v: EpisodeVersion) -> EpisodeVersionSummary:
    return EpisodeVersionSummary(**{k: getattr(v, k) for k in EpisodeVersionSummary.model_fields})


def _episode(session: Session, ep: Episode, full: bool = True) -> EpisodeOut:
    s = session.get(Series, ep.series_id)
    out = EpisodeOut(id=ep.id, code=ep.code, series_id=s.id, series_code=s.code, number=ep.number, title=ep.title,
                     concept=ep.concept, created_at=ep.created_at)
    if full:
        out.script_versions = [_sv(v) for v in svc.script_versions(session, ep)]
        out.storyboards = [_sb(b) for b in svc.storyboards(session, ep)]
        out.versions = [_evs(v) for v in svc.versions(session, ep)]
    return out


@episodes_router.post("", status_code=status.HTTP_201_CREATED, response_model=EpisodeOut)
def create_episode(body: EpisodeCreate, session: Session = Depends(get_session),
                   who: Principal = Depends(require("episode.manage"))) -> EpisodeOut:
    s = svc.get_series(session, body.series_id)
    ep = svc.create_episode(session, s, title=body.title, concept=body.concept, number=body.number,
                            created_by=who.user_id)
    audit(session, who, "episode.created", "episode", ep.id, code=ep.code, series=s.code)
    session.commit()
    return _episode(session, ep)


@episodes_router.get("", response_model=Page[EpisodeOut], summary="Episodes, newest first")
def list_episodes(series: str | None = None, session: Session = Depends(get_session),
                  _: Principal = Depends(require("asset.read"))) -> Page[EpisodeOut]:
    q = select(Episode).order_by(Episode.id.desc())
    if series:
        q = q.where(Episode.series_id == svc.get_series(session, series).id)
    return Page(items=[_episode(session, e, full=False) for e in session.scalars(q)])


@episodes_router.get("/{ref}", response_model=EpisodeOut, summary="An episode with its scripts, storyboards, versions")
def get_episode(ref: str, session: Session = Depends(get_session),
                _: Principal = Depends(require("asset.read"))) -> EpisodeOut:
    return _episode(session, svc.get_episode(session, ref))


# ---------------------------------------------------------------- scripts
class EpisodeIssue(BaseModel):
    path: str
    code: str
    message: str
    severity: str = "error"


class ScriptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: dict[str, Any] = Field(description="ScriptSpecification (script/1)")
    based_on_id: uuid.UUID | None = None
    ai_generation_id: uuid.UUID | None = Field(
        default=None, description="when this keeps an AI suggestion (as is or edited): its generation log")
    notes: str = Field(default="", max_length=2000)


class ScriptSaved(BaseModel):
    version: ScriptVersionOut
    issues: list[EpisodeIssue]


class ScriptCheck(BaseModel):
    ok: bool
    issues: list[EpisodeIssue]


@episodes_router.post("/{ref}/scripts/validate", response_model=ScriptCheck,
                      summary="Dry run: check a script against the series")
def validate_script(ref: str, body: ScriptBody, session: Session = Depends(get_session),
                    _: Principal = Depends(require("asset.read"))) -> ScriptCheck:
    from studio_contracts.episode import ScriptSpecification

    ep = svc.get_episode(session, ref)
    spec = svc._parse(ScriptSpecification, {**body.spec, "episode_id": ep.code}, "script")
    issues = svc.check_script(session, ep, spec)
    return ScriptCheck(ok=not any(i.severity == "error" for i in issues), issues=[EpisodeIssue(**i.model_dump()) for i in issues])


@episodes_router.post("/{ref}/scripts", status_code=status.HTTP_201_CREATED, response_model=ScriptSaved,
                      summary="Save a script version (never edited; an edit is the next number)")
def save_script(ref: str, body: ScriptBody, session: Session = Depends(get_session),
                who: Principal = Depends(require("episode.manage"))) -> ScriptSaved:
    from app.models.ai import AIGeneration

    ep = svc.get_episode(session, ref)
    base = svc.get_script_version(session, ep, body.based_on_id) if body.based_on_id else None
    source = "manual"
    if body.ai_generation_id:
        gen = session.get(AIGeneration, body.ai_generation_id)
        if gen is None or gen.task != "generate_script":
            raise ApiError(422, "not_a_script_generation", "ai_generation_id is not a script generation")
        source = "ai"
    v, issues = svc.add_script_version(session, ep, body.spec, source=source, ai_generation_id=body.ai_generation_id,
                                       based_on=base, notes=body.notes, created_by=who.user_id)
    audit(session, who, "script.version_saved", "script_version", v.id, episode=ep.code, number=v.number,
          source=source, digest=v.digest)
    session.commit()
    return ScriptSaved(version=_sv(v), issues=[EpisodeIssue(**i.model_dump()) for i in issues])


class GenerateScriptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concept: str | None = Field(default=None, max_length=4000, description="defaults to the episode's concept")
    target_duration_s: float = Field(default=60.0, gt=5, le=1800)
    provider: str | None = None


@episodes_router.post("/{ref}/scripts/generate", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut,
                      summary="Ask the AI for a script suggestion (a job; nothing is saved until a person saves it)")
def generate_script(ref: str, body: GenerateScriptBody, response: Response, session: Session = Depends(get_session),
                    idempotency_key: str | None = Header(default=None, max_length=200),
                    who: Principal = Depends(require("episode.manage"))) -> JobOut:
    ep = svc.get_episode(session, ref)
    concept = (body.concept or ep.concept).strip()
    if not concept:
        raise ApiError(422, "no_concept", "give a concept, or set the episode's")
    params = {"episode_id": str(ep.id), "concept": concept, "target_duration_s": body.target_duration_s,
              "provider": body.provider}
    job, created = submit_and_dispatch(session, who, "generate_script", params, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return job_out(job)


# ---------------------------------------------------------------- storyboards
class StoryboardBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_version_id: uuid.UUID
    source: Literal["rule", "manual", "ai"] = Field(
        default="rule", description="rule: plan it from the script; manual: the shots given; ai: keep an AI suggestion")
    spec: dict[str, Any] | None = Field(default=None, description="StoryboardSpecification, for manual and ai")
    based_on_id: uuid.UUID | None = None
    ai_generation_id: uuid.UUID | None = None
    notes: str = Field(default="", max_length=2000)


class StoryboardSaved(BaseModel):
    storyboard: StoryboardOut
    issues: list[EpisodeIssue]


@episodes_router.post("/{ref}/storyboards", status_code=status.HTTP_201_CREATED, response_model=StoryboardSaved,
                      summary="Save a storyboard: planned by rule, edited by hand, or an AI suggestion kept")
def save_storyboard(ref: str, body: StoryboardBody, session: Session = Depends(get_session),
                    who: Principal = Depends(require("episode.manage"))) -> StoryboardSaved:
    from app.models.ai import AIGeneration

    ep = svc.get_episode(session, ref)
    sv = svc.get_script_version(session, ep, body.script_version_id)
    base = svc.get_storyboard(session, body.based_on_id) if body.based_on_id else None
    if body.source == "rule":
        if body.spec is not None:
            raise ApiError(422, "spec_not_allowed", "a rule storyboard is planned from the script; send source manual")
        spec: Any = svc.plan(session, ep, sv)
    else:
        if body.spec is None:
            raise ApiError(422, "spec_required", f"a {body.source} storyboard needs its shots")
        spec = body.spec
    if body.source == "ai":
        gen = session.get(AIGeneration, body.ai_generation_id) if body.ai_generation_id else None
        if gen is None or gen.task != "suggest_shots":
            raise ApiError(422, "not_a_shot_suggestion", "an ai storyboard needs the ai_generation_id of its suggestion")
    sb, issues = svc.add_storyboard(session, ep, sv, spec, source=body.source, ai_generation_id=body.ai_generation_id,
                                    based_on=base, notes=body.notes, created_by=who.user_id)
    audit(session, who, "storyboard.saved", "storyboard", sb.id, episode=ep.code, number=sb.number, source=sb.source)
    session.commit()
    return StoryboardSaved(storyboard=_sb(sb), issues=[EpisodeIssue(**i.model_dump()) for i in issues])


class SuggestShotsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_version_id: uuid.UUID
    provider: str | None = None


@episodes_router.post("/{ref}/storyboards/suggest", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut,
                      summary="Ask the AI to suggest shots for a script version (a job; nothing is saved)")
def suggest_shots(ref: str, body: SuggestShotsBody, response: Response, session: Session = Depends(get_session),
                  idempotency_key: str | None = Header(default=None, max_length=200),
                  who: Principal = Depends(require("episode.manage"))) -> JobOut:
    ep = svc.get_episode(session, ref)
    sv = svc.get_script_version(session, ep, body.script_version_id)
    params = {"episode_id": str(ep.id), "script_version_id": str(sv.id), "provider": body.provider}
    job, created = submit_and_dispatch(session, who, "suggest_shots", params, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return job_out(job)


@storyboards_router.get("/{sb_id}", response_model=StoryboardOut)
def get_storyboard(sb_id: uuid.UUID, session: Session = Depends(get_session),
                   _: Principal = Depends(require("asset.read"))) -> StoryboardOut:
    return _sb(svc.get_storyboard(session, sb_id))


class VoiceLine(BaseModel):
    shot_id: str
    line: int
    character_id: str
    text: str
    audio: str
    status: str
    new: bool
    job_id: uuid.UUID | None


class VoiceOut(BaseModel):
    lines: list[VoiceLine]
    jobs: list[JobOut]


@storyboards_router.post("/{sb_id}/voice", status_code=status.HTTP_202_ACCEPTED, response_model=VoiceOut,
                         summary="Speak every line without a recording with its character's voice (media jobs)")
def voice(sb_id: uuid.UUID, session: Session = Depends(get_session),
          who: Principal = Depends(require("episode.manage"))) -> VoiceOut:
    _need(who, "synthesize_speech")
    sb = svc.get_storyboard(session, sb_id)
    ep = session.get(Episode, sb.episode_id)
    plan = svc.plan_voices(session, ep, sb, who.user_id)
    spec = svc.storyboard_spec(sb)
    jobs, lines = [], []
    for i, j, clip, new in plan.lines:
        job = None
        if new:
            job, _ = service.submit(session, "synthesize_speech", {"audio_id": str(clip.id), "text": clip.dialog_text,
                                                                   "voice": clip.voice}, requested_by=who.user_id)
            clip.job_id = job.id
            jobs.append(job)
            audit(session, who, "audio.speech_requested", "audio_clip", clip.id, code=clip.code)
        ln = spec.shots[i].dialogue[j]
        lines.append((spec.shots[i].shot_id, j, ln, clip, new, job))
    session.commit()
    _dispatch(session, jobs)
    return VoiceOut(lines=[VoiceLine(shot_id=s, line=j, character_id=ln.character_id, text=ln.text, audio=c.code,
                                     status=c.status, new=new, job_id=c.job_id) for s, j, ln, c, new, _ in lines],
                    jobs=[job_out(session.get(Job, j.id)) for j in jobs])


@storyboards_router.post("/{sb_id}/voiced", status_code=status.HTTP_201_CREATED, response_model=StoryboardSaved,
                         summary="A new storyboard with each line's recording, shots lengthened to fit")
def make_voiced(sb_id: uuid.UUID, session: Session = Depends(get_session),
                who: Principal = Depends(require("episode.manage"))) -> StoryboardSaved:
    sb = svc.get_storyboard(session, sb_id)
    ep = session.get(Episode, sb.episode_id)
    new, issues = svc.voiced(session, ep, sb, who.user_id)
    audit(session, who, "storyboard.saved", "storyboard", new.id, episode=ep.code, number=new.number, source="voiced")
    session.commit()
    return StoryboardSaved(storyboard=_sb(new), issues=[EpisodeIssue(**i.model_dump()) for i in issues])


# ---------------------------------------------------------------- episode versions
class CastBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character: str = Field(pattern=r"^CHAR-\d{3,}$")
    key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    character_version: Pin | None = None
    outfit: Pin | None = None
    accessories: list[AccessoryUse] = Field(default_factory=list, max_length=8)
    voice: VoiceProfile | None = None


class VersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storyboard_id: uuid.UUID
    cast: list[CastBody] = Field(default_factory=list, max_length=12,
                                 description="overrides; every script character defaults to the series member")
    environments: list[Pin] = Field(default_factory=list, max_length=20,
                                    description="overrides; else the series' pinned version, else recommended")
    action_animations: dict[str, str] = Field(default_factory=dict)
    music: list[AudioCue] = Field(default_factory=list, max_length=8)
    sfx: list[AudioCue] = Field(default_factory=list, max_length=100)
    dialogue_volume: float = Field(default=1.0, ge=0, le=2)
    render: dict[str, Any] = Field(default_factory=dict, description="RenderSettings fields over the series'")
    notes: str = Field(default="", max_length=2000)
    allow_verified_fits: bool = False


class VersionShotOut(BaseModel):
    position: int
    shot_id: str
    scene_id: uuid.UUID
    scene_code: str
    scene_version_id: uuid.UUID
    scene_version: int
    start_s: float
    duration_s: float
    transition: dict[str, Any]
    build_status: str
    verify_status: str


class ClosureOut(BaseModel):
    asset_version_id: uuid.UUID
    kind: str
    display: str


class AudioUseOut(BaseModel):
    code: str
    role: str
    kind: str
    source: str
    status: str
    duration_s: float | None
    voice: dict[str, Any] | None
    text: str | None


class EpisodeVersionOut(EpisodeVersionSummary):
    episode_id: uuid.UUID
    episode_code: str
    manifest: dict[str, Any]
    shots: list[VersionShotOut]
    closure: list[ClosureOut]
    audio: list[AudioUseOut]
    progress: dict[str, Any]
    issues: list[EpisodeIssue] = Field(default_factory=list)
    new_scene_versions: int | None = None


def _version(session: Session, v: EpisodeVersion, issues: list | None = None, new: int | None = None) -> EpisodeVersionOut:
    ep = session.get(Episode, v.episode_id)
    shots = []
    for s, sv in svc.shot_versions(session, v):
        sc = session.get(Scene, sv.scene_id)
        shots.append(VersionShotOut(position=s.position, shot_id=s.shot_id, scene_id=sc.id, scene_code=sc.code,
                                    scene_version_id=sv.id, scene_version=sv.number, start_s=s.start_s,
                                    duration_s=s.duration_s, transition=s.transition,
                                    build_status=scene_svc.build_status(session, sv),
                                    verify_status=scene_svc.verify_status(session, sv)))
    return EpisodeVersionOut(**_evs(v).model_dump(), episode_id=ep.id, episode_code=ep.code, manifest=v.manifest,
                             shots=shots,
                             closure=[ClosureOut(asset_version_id=a.asset_version_id, kind=a.kind, display=a.display)
                                      for a in svc.closure(session, v)],
                             audio=[AudioUseOut(code=c.code, role=u.role, kind=c.kind, source=c.source, status=c.status,
                                                duration_s=c.duration_s, voice=c.voice, text=c.dialog_text)
                                    for u, c in svc.audio_of(session, v)],
                             progress=svc.progress(session, v),
                             issues=[EpisodeIssue(**i.model_dump()) for i in issues or []], new_scene_versions=new)


def _create_version(ref: str, body: VersionBody, session: Session, who: Principal) -> EpisodeVersionOut:
    if body.allow_verified_fits and not who.can("asset.publish"):
        raise ApiError(403, "forbidden", "accepting a verified but unapproved fit is a director's override",
                       required="asset.publish")
    ep = svc.get_episode(session, ref)
    sb = svc.get_storyboard(session, body.storyboard_id)
    cast = [svc.CastIn(character=c.character, key=c.key, character_version=c.character_version, outfit=c.outfit,
                       accessories=[a.model_dump(mode="json") for a in c.accessories],
                       voice=c.voice.model_dump(mode="json") if c.voice else None) for c in body.cast]
    extra = {"action_animations": body.action_animations, "music": [m.model_dump(mode="json") for m in body.music],
             "sfx": [m.model_dump(mode="json") for m in body.sfx], "dialogue_volume": body.dialogue_volume,
             "render": body.render}
    v, c = svc.create_version(session, ep, sb, cast=cast, environments=body.environments, extra=extra,
                              allow_verified_fits=body.allow_verified_fits, notes=body.notes, created_by=who.user_id)
    new = sum(1 for s in c.shots if s[5])
    audit(session, who, "episode.version_created", "episode_version", v.id, episode=ep.code, number=v.number,
          digest=v.digest, new_scene_versions=new)
    session.commit()
    return _version(session, svc.get_version(session, v.id), c.issues, new)


@episodes_router.post("/{ref}/versions", status_code=status.HTTP_201_CREATED, response_model=EpisodeVersionOut,
                      summary="Compile a storyboard into an episode version: one scene version per shot, exact pins")
def create_version(ref: str, body: VersionBody, session: Session = Depends(get_session),
                   who: Principal = Depends(require("episode.manage"))) -> EpisodeVersionOut:
    return _create_version(ref, body, session, who)


@episodes_router.post("/{ref}/scenes", status_code=status.HTTP_201_CREATED, response_model=EpisodeVersionOut,
                      summary="SRS 23 name for compiling the episode's scenes (same as POST /episodes/{id}/versions)")
def create_scenes(ref: str, body: VersionBody, session: Session = Depends(get_session),
                  who: Principal = Depends(require("episode.manage"))) -> EpisodeVersionOut:
    return _create_version(ref, body, session, who)


class EpisodeSceneOut(BaseModel):
    shot_id: str
    scene_id: uuid.UUID
    scene_code: str
    versions: int


@episodes_router.get("/{ref}/scenes", response_model=list[EpisodeSceneOut], summary="The scene each shot lives in")
def list_scenes(ref: str, session: Session = Depends(get_session),
                _: Principal = Depends(require("asset.read"))) -> list[EpisodeSceneOut]:
    ep = svc.get_episode(session, ref)
    out = []
    for row in session.scalars(select(EpisodeScene).where(EpisodeScene.episode_id == ep.id).order_by(EpisodeScene.shot_id)):
        sc = session.get(Scene, row.scene_id)
        out.append(EpisodeSceneOut(shot_id=row.shot_id, scene_id=sc.id, scene_code=sc.code, versions=len(sc.versions)))
    return out


@versions_router.get("/{vid}", response_model=EpisodeVersionOut)
def get_version(vid: uuid.UUID, session: Session = Depends(get_session),
                _: Principal = Depends(require("asset.read"))) -> EpisodeVersionOut:
    return _version(session, svc.get_version(session, vid))


class ShotJobs(BaseModel):
    version: EpisodeVersionOut
    jobs: list[JobOut]


class BuildBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previews: ScenePreviewSettings = Field(default_factory=lambda: ScenePreviewSettings(width=320, height=180, samples=4, frames=[1]))


def _scene_jobs(session: Session, who: Principal, v: EpisodeVersion, kind: str, previews: ScenePreviewSettings) -> list[Job]:
    jobs = []
    for _, sv in svc.shot_versions(session, v):
        sv = scene_svc.get_version(session, sv.scene_id, sv.id, lock=True)
        if kind == "assemble_scene":
            if scene_svc.build_status(session, sv) not in ("none", "failed"):
                continue
            scene_svc.check_pins_still_usable(session, sv)
            params = {"scene_version_id": str(sv.id), "resolved": sv.resolved, "previews": previews.model_dump()}
            job, _ = service.submit(session, kind, params, requested_by=who.user_id)
            scene_svc.begin_assemble(session, sv, job.id)
        else:
            if scene_svc.build_status(session, sv) != "built" or scene_svc.verify_status(session, sv) == "queued":
                continue
            params = {"scene_version_id": str(sv.id), "resolved": sv.resolved,
                      "scene_blend_sha256": sv.scene_blend_sha256, "previews": previews.model_dump()}
            job, _ = service.submit(session, kind, params, requested_by=who.user_id)
            scene_svc.begin_verify(session, sv, job.id)
        jobs.append(job)
    return jobs


@versions_router.post("/{vid}/build", status_code=status.HTTP_202_ACCEPTED, response_model=ShotJobs,
                      summary="Build every shot's scene.blend that is not built yet (Blender jobs)")
def build(vid: uuid.UUID, body: BuildBody | None = None, session: Session = Depends(get_session),
          who: Principal = Depends(require("scene.edit"))) -> ShotJobs:
    v = svc.get_version(session, vid)
    jobs = _scene_jobs(session, who, v, "assemble_scene", (body or BuildBody()).previews)
    audit(session, who, "episode.build_requested", "episode_version", v.id, jobs=len(jobs))
    session.commit()
    _dispatch(session, jobs)
    return ShotJobs(version=_version(session, svc.get_version(session, vid)),
                    jobs=[job_out(session.get(Job, j.id)) for j in jobs])


@versions_router.post("/{vid}/reopen", status_code=status.HTTP_202_ACCEPTED, response_model=ShotJobs,
                      summary="Reopen every shot's saved scene.blend from its pins and check it (Blender jobs)")
def reopen(vid: uuid.UUID, body: BuildBody | None = None, session: Session = Depends(get_session),
           who: Principal = Depends(require("scene.edit"))) -> ShotJobs:
    v = svc.get_version(session, vid)
    jobs = _scene_jobs(session, who, v, "verify_scene", (body or BuildBody()).previews)
    audit(session, who, "episode.reopen_requested", "episode_version", v.id, jobs=len(jobs))
    session.commit()
    _dispatch(session, jobs)
    return ShotJobs(version=_version(session, svc.get_version(session, vid)),
                    jobs=[job_out(session.get(Job, j.id)) for j in jobs])


@versions_router.post("/{vid}/lock", response_model=EpisodeVersionOut,
                      summary="Lock the version: its shots, pins and audio never change; it can be rendered")
def lock(vid: uuid.UUID, session: Session = Depends(get_session),
         who: Principal = Depends(require("episode.manage"))) -> EpisodeVersionOut:
    v = svc.get_version(session, vid, lock=True)
    svc.lock(session, v, who.user_id)
    audit(session, who, "episode.version_locked", "episode_version", v.id, digest=v.digest,
          closure=[a.display for a in svc.closure(session, v)])
    session.commit()
    return _version(session, svc.get_version(session, vid))


# ================================================================ render jobs
class RenderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_version_id: uuid.UUID
    settings: dict[str, Any] = Field(default_factory=dict, description="RenderSettings over the version's")


class RenderShotOut(BaseModel):
    position: int
    shot_id: str
    scene_version_id: uuid.UUID
    job: JobOut
    frames: int | None


class RenderOutputOut(BaseModel):
    id: uuid.UUID
    kind: str
    shot_id: str | None
    scene_version_id: uuid.UUID | None
    storage_key: str
    sha256: str | None
    size_bytes: int | None
    frame_count: int | None
    meta: dict[str, Any]
    url: str | None = None


class RenderOut(BaseModel):
    id: uuid.UUID
    code: str
    episode_id: uuid.UUID
    episode_version_id: uuid.UUID
    status: str
    progress: dict[str, Any]
    settings: dict[str, Any]
    asset_versions: list[str]
    blender_version: str | None
    output_location: str | None
    error: dict[str, Any] | None
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    shots: list[RenderShotOut]
    export_job: JobOut | None
    outputs: list[RenderOutputOut]
    video_url: str | None = None


def _render(session: Session, rj: RenderJob, urls: bool = True) -> RenderOut:
    rsvc.refresh(session, rj)
    jobs = {j.id: j for j in rsvc.jobs_of(session, rj)}
    store = Storage.for_bucket("work") if urls else None
    outputs = []
    for o in rj.outputs:
        url = store.presign_get(o.storage_key, filename=o.storage_key.rsplit("/", 1)[-1]) \
            if store and o.kind != "frames" else None
        outputs.append(RenderOutputOut(id=o.id, kind=o.kind, shot_id=o.shot_id, scene_version_id=o.scene_version_id,
                                       storage_key=o.storage_key, sha256=o.sha256, size_bytes=o.size_bytes,
                                       frame_count=o.frame_count, meta=o.meta, url=url))
    video = next((o for o in outputs if o.kind == "video"), None)
    return RenderOut(id=rj.id, code=rj.code, episode_id=rj.episode_id, episode_version_id=rj.episode_version_id,
                     status=rj.status, progress=rsvc.progress(session, rj), settings=rj.settings,
                     asset_versions=rj.asset_versions, blender_version=rj.blender_version,
                     output_location=rj.output_location, error=rj.error, cancel_requested=rj.cancel_requested,
                     created_at=rj.created_at, started_at=rj.started_at, finished_at=rj.finished_at,
                     shots=[RenderShotOut(position=s.position, shot_id=s.shot_id, scene_version_id=s.scene_version_id,
                                          job=job_out(jobs[s.job_id]), frames=s.frames) for s in rj.shots],
                     export_job=job_out(jobs[rj.export_job_id]) if rj.export_job_id else None,
                     outputs=outputs, video_url=video.url if video else None)


@renders_router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=RenderOut,
                     summary="Render a locked episode version: a Blender job per shot, then the FFmpeg export")
def create_render(body: RenderCreate, session: Session = Depends(get_session),
                  who: Principal = Depends(require("render.submit"))) -> RenderOut:
    _need(who, "render_scene")
    v = svc.get_version(session, body.episode_version_id)
    rj, jobs = rsvc.create(session, v, body.settings, who.user_id)
    audit(session, who, "render.submitted", "render_job", rj.id, code=rj.code, episode_version=str(v.id),
          settings=rj.settings, asset_versions=rj.asset_versions)
    session.commit()
    _dispatch(session, jobs)
    return _render(session, rsvc.get(session, str(rj.id)))


@renders_router.get("", response_model=Page[RenderOut], summary="Render jobs, newest first")
def list_renders(episode: str | None = None, limit: int = Query(default=20, ge=1, le=100),
                 session: Session = Depends(get_session), _: Principal = Depends(require("job.read"))) -> Page[RenderOut]:
    q = select(RenderJob).order_by(RenderJob.id.desc()).limit(limit)
    if episode:
        q = q.where(RenderJob.episode_id == svc.get_episode(session, episode).id)
    items = [_render(session, rj, urls=False) for rj in session.scalars(q)]
    session.commit()
    return Page(items=items)


@renders_router.get("/{ref}", response_model=RenderOut, summary="Status, progress, settings, pins and outputs")
def get_render(ref: str, session: Session = Depends(get_session), _: Principal = Depends(require("job.read"))) -> RenderOut:
    out = _render(session, rsvc.get(session, ref))
    session.commit()  # a status the jobs moved on is saved
    return out


@renders_router.post("/{ref}/cancel", response_model=RenderOut, summary="Cancel every shot and the export")
def cancel_render(ref: str, session: Session = Depends(get_session),
                  who: Principal = Depends(require("job.cancel"))) -> RenderOut:
    rj = rsvc.cancel(session, rsvc.get(session, ref, lock=True))
    audit(session, who, "render.cancel_requested", "render_job", rj.id)
    session.commit()
    return _render(session, rsvc.get(session, ref))


@renders_router.post("/{ref}/retry", response_model=RenderOut, summary="Run the failed jobs of a failed render again")
def retry_render(ref: str, session: Session = Depends(get_session),
                 who: Principal = Depends(require("render.submit"))) -> RenderOut:
    rj = rsvc.get(session, ref, lock=True)
    jobs = rsvc.retry(session, rj)
    audit(session, who, "render.retried", "render_job", rj.id, jobs=[str(j.id) for j in jobs])
    session.commit()
    _dispatch(session, jobs)
    return _render(session, rsvc.get(session, ref))


class RenderLog(BaseModel):
    job_id: uuid.UUID
    type: str
    shot_id: str | None
    status: str
    url: str | None


@renders_router.get("/{ref}/logs", response_model=list[RenderLog], summary="Each job's captured log")
def render_logs(ref: str, session: Session = Depends(get_session),
                _: Principal = Depends(require("job.read"))) -> list[RenderLog]:
    rj = rsvc.get(session, ref)
    store = Storage.for_bucket("work")
    shots = {s.job_id: s.shot_id for s in rj.shots}
    return [RenderLog(job_id=j.id, type=j.type, shot_id=shots.get(j.id), status=j.status,
                      url=store.presign_get(j.log_key) if j.log_key else None) for j in rsvc.jobs_of(session, rj)]


@renders_router.get("/{ref}/video", summary="The episode MP4: 302 to a presigned URL",
                    responses={302: {"description": "redirect"}, 200: {"model": DownloadOut}})
def render_video(ref: str, redirect: bool = True, session: Session = Depends(get_session),
                 _: Principal = Depends(require("job.read"))):
    rj = rsvc.get(session, ref)
    if not rj.output_location:
        raise ApiError(409, "no_video", f"{rj.code} is {rj.status}")
    store = Storage.for_bucket("work")
    url = store.presign_get(rj.output_location, filename=f"{rj.code}.mp4")
    if redirect:
        return RedirectResponse(url, status_code=302)
    return DownloadOut(url=url, expires_in_s=store.presign_expiry_s)


@renders_router.get("/{ref}/outputs/{output_id}", summary="One render output, or one frame of a shot's frames",
                    responses={302: {"description": "redirect"}, 200: {"model": DownloadOut}})
def render_output(ref: str, output_id: uuid.UUID, frame: int | None = Query(default=None, ge=1,
                  description="for a frames output: which frame, counted from 1 in the shot"),
                  redirect: bool = True, session: Session = Depends(get_session),
                  _: Principal = Depends(require("job.read"))):
    rj = rsvc.get(session, ref)
    out = next((o for o in rj.outputs if o.id == output_id), None)
    if out is None:
        raise ApiError(404, "not_found", f"{rj.code} has no output {output_id}")
    if out.kind == "frames":
        frames = (out.meta or {}).get("frames", [])
        if frame is None or frame > len(frames):
            raise ApiError(422, "frame_required", f"{out.shot_id} has frames 1 to {len(frames)}; ask for one")
        key, name = frames[frame - 1]["storage_key"], f"{rj.code}-{out.shot_id}-{frame:04d}.png"
    else:
        key, name = out.storage_key, out.storage_key.rsplit("/", 1)[-1]
    store = Storage.for_bucket("work")
    url = store.presign_get(key, filename=name)
    if redirect:
        return RedirectResponse(url, status_code=302)
    return DownloadOut(url=url, expires_in_s=store.presign_expiry_s)
