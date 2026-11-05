"""``generate_script`` and ``suggest_shots``: AI suggestions for an episode (SRS 14, 15, 24).

The same pattern as the character and outfit tasks. The model fills a **closed** proposal:
characters and locations are ``Literal`` values made from the series' cast and the
published environments, actions from the clip catalog, emotions from the rig's
expressions, marks and camera marks from the environments' layouts. Code assigns the
scene and shot ids, copies each line's text from the script, times the lines, and checks the
result with ``validate_script`` / ``validate_storyboard``; one repair turn is allowed. Every
call is logged in ``ai_generations``.

The result is a **suggestion** in the job result: nothing is saved. A person reviews it,
edits it if they want, and saves it as a script version or storyboard (``source = 'ai'``
with the generation's id), which is when it can be used.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from studio_contracts.character import Issue
from studio_contracts.episode import (
    ACTION_CLIPS,
    EXPRESSIONS,
    ScriptSpecification,
    StoryboardSpecification,
    line_seconds,
    validate_script,
    validate_storyboard,
)
from studio_contracts.jobs import GenerateScriptParams, SuggestShotsParams

from app.ai.providers import LLMRequest, LLMSettings
from app.ai.tasks.interpret_character import AIRefused, _lit, _log, _Strict

log = logging.getLogger("studio.ai")
ACTIONS = tuple(sorted(ACTION_CLIPS))


# ---------------------------------------------------------------- context from the database
@dataclass
class Cast:
    code: str
    name: str
    description: str
    role: str


@dataclass
class Place:
    code: str
    name: str
    marks: list[str]
    cameras: list[str]


@dataclass
class Context:
    episode_code: str
    series: str
    cast: list[Cast]
    places: list[Place]
    script: ScriptSpecification | None = None


def load_context(session: Session, episode_id: uuid.UUID, script_version_id: uuid.UUID | None = None) -> Context:
    from app.assets import episodes as ep_svc
    from app.models.assets import AssetVersion
    from app.models.characters import Character
    from app.models.episodes import Episode, ScriptVersion, Series

    ep = session.get(Episode, episode_id)
    if ep is None:
        raise ValueError(f"no episode {episode_id}")
    series = session.get(Series, ep.series_id)
    cast = []
    for m in series.members:
        ch = session.get(Character, m.character_id)
        cast.append(Cast(code=ch.code, name=ch.name, description=(ch.description or "")[:300], role=m.role))
    if not cast:
        raise ValueError(f"{series.code} has no cast; add characters to the series first")
    codes = sorted(set(session.scalars(select(AssetVersion.display_code).where(
        AssetVersion.kind == "environment", AssetVersion.status == "published"))))[:20]
    layouts = ep_svc.environment_layouts(session, set(codes), series)
    places = []
    for code in codes:
        ev = ep_svc.latest_usable(session, "environment", code)
        lay = layouts.get(code, {})
        places.append(Place(code=code, name=ev.owner.name if ev else code,
                            marks=[m["name"] for m in lay.get("marks", []) if m.get("kind", "spawn") == "spawn"],
                            cameras=[c["name"] for c in lay.get("cameras", [])]))
    script = None
    if script_version_id is not None:
        sv = session.get(ScriptVersion, script_version_id)
        if sv is None:
            raise ValueError(f"no script version {script_version_id}")
        script = ScriptSpecification.model_validate(sv.spec)
    return Context(episode_code=ep.code, series=series.name, cast=cast, places=places, script=script)


# ---------------------------------------------------------------- shared call loop
@dataclass
class Suggestion:
    spec: Any
    issues: list[Issue]
    provider: str
    model: str
    stop: str = "ok"
    calls: int = 0
    repair_turns: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    proposal: dict[str, Any] | None = None
    prompt_sha256: str = ""

    @property
    def valid(self) -> bool:
        return self.spec is not None and not any(i.severity == "error" for i in self.issues)


def _ask(task: str, llm: LLMSettings, provider_name: str | None, system: str, user, output_model, convert,
         semantic_repairs: int) -> Suggestion:
    provider, model = llm.for_task(task, provider_name)
    out = Suggestion(spec=None, issues=[], provider=provider.name, model=model)
    issues: list[Issue] | None = None
    previous: str | None = None
    for round_ in range(semantic_repairs + 1):
        text = user(issues, previous)
        if round_ == 0:
            out.prompt_sha256 = hashlib.sha256(f"{system}\n\n{text}".encode()).hexdigest()
        result = provider.complete(LLMRequest(model=model, system=system, user=text, output_model=output_model,
                                              max_tokens=6000, json_repairs=llm.json_repairs))
        out.calls += result.calls
        out.repair_turns += max(0, result.calls - 1) + (1 if round_ else 0)
        for k, v in result.usage.items():
            out.usage[k] = out.usage.get(k, 0) + v
        if result.stop != "ok" or result.parsed is None:
            out.stop = result.stop
            raise AIRefused(f"{provider.name} stopped with {result.stop} {result.detail}".strip())
        out.proposal = result.parsed.model_dump()
        out.spec, out.issues = convert(result.parsed)
        issues = [i for i in out.issues if i.severity == "error"]
        if not issues:
            return out
        previous = result.parsed.model_dump_json()
        log.info("%s: proposal has %d issues; asking for a repair", task, len(issues))
    return out


def _repair_text(issues: list[Issue] | None, previous: str | None) -> str:
    if not issues:
        return ""
    return ("\n\nYour previous proposal was:\n" + (previous or "") +
            "\n\nIt failed these checks; return a corrected complete proposal:\n" +
            "\n".join(f"- {i.path}: {i.message}" for i in issues))


def _finish(sessions, task: str, job_id: uuid.UUID, record_in: dict[str, Any], out: Suggestion,
            extra: dict[str, Any]) -> dict[str, Any]:
    spec = out.spec.model_dump(mode="json") if out.spec is not None else None
    data = {"valid": out.valid, "spec": spec, "issues": [i.model_dump() for i in out.issues],
            "notes": (out.proposal or {}).get("notes", ""), "provider": out.provider, "model": out.model,
            "usage": out.usage, **extra}
    gen_id = _log(sessions, job_id=job_id, task=task, input_refs=record_in, provider=out.provider, model=out.model,
                  prompt_sha256=out.prompt_sha256, output=spec, issues=data["issues"], valid=out.valid, stop=out.stop,
                  repair_turns=out.repair_turns, calls=out.calls, usage=out.usage, error=None)
    data["ai_generation_id"] = str(gen_id)
    if not out.valid:
        raise ValueError(f"the AI {task} suggestion still fails validation after repair: "
                         + json.dumps(data["issues"])[:1500])
    return data


def _refused(sessions, task: str, job_id, record_in, llm: LLMSettings, provider_name, exc) -> None:
    provider, model = llm.choice_for(task, provider_name)
    _log(sessions, job_id=job_id, task=task, input_refs=record_in, provider=provider, model=model, prompt_sha256="",
         output=None, issues=None, valid=False, stop="refusal", repair_turns=0, calls=1, usage={}, error=str(exc))


# ================================================================ generate_script
def script_model(ctx: Context) -> type[BaseModel]:
    chars = _lit(c.code for c in ctx.cast)
    loc = (_lit(p.code for p in ctx.places) | None) if ctx.places else type(None)
    event = create_model("ProposedEvent", __base__=_Strict, character=(chars, ...), action=(_lit(ACTIONS), ...),
                         target=(str | None, Field(default=None, max_length=80)))
    line = create_model("ProposedLine", __base__=_Strict, character=(chars, ...),
                        text=(str, Field(min_length=1, max_length=240)),
                        emotion=(_lit(EXPRESSIONS) | None, Field(default=None)))
    scene = create_model(
        "ProposedScene", __base__=_Strict,
        location=(loc, Field(default=None)),
        description=(str, Field(default="", max_length=600)),
        emotional_context=(str, Field(default="", max_length=200)),
        characters=(list[chars], Field(min_length=1, max_length=8)),  # type: ignore[valid-type]
        events=(list[event], Field(default_factory=list, max_length=12)),  # type: ignore[valid-type]
        dialogue=(list[line], Field(default_factory=list, max_length=30)),  # type: ignore[valid-type]
    )
    return create_model("ScriptProposal", __base__=_Strict, title=(str, Field(min_length=1, max_length=120)),
                        synopsis=(str, Field(default="", max_length=1500)),
                        scenes=(list[scene], Field(min_length=1, max_length=12)),  # type: ignore[valid-type]
                        notes=(str, Field(default="", max_length=600)))


def script_system(ctx: Context) -> str:
    cast = "\n".join(f"- {c.code}: {c.name} ({c.role}) {c.description}".rstrip() for c in ctx.cast)
    places = "\n".join(f"- {p.code}: {p.name}" for p in ctx.places) or "- (none: scenes have no location)"
    return f"""You write short scripts for the family 3D cartoon series "{ctx.series}".
Write the episode the user describes as structured scenes. Refer to characters and locations only by the
codes listed; never invent characters or places.

Characters:
{cast}

Locations:
{places}

Rules:
- Each scene lists the characters in it; events and lines only use characters in that scene.
- Actions are one of: {", ".join(ACTIONS)}. Emotions are one of: {", ".join(EXPRESSIONS)}.
- Lines are short spoken sentences (under 20 words), suitable for children. No real brands or people.
- Keep to the target length: about 2.6 spoken words per second plus a few seconds per scene.
A person reviews and edits your script before anything is produced."""


def script_user(params: GenerateScriptParams):
    def text(issues, previous):
        return (f"Episode concept:\n{params.concept.strip()}\n\nTarget length: about {params.target_duration_s:.0f} "
                f"seconds." + _repair_text(issues, previous))
    return text


def script_to_spec(ctx: Context, p: BaseModel) -> ScriptSpecification:
    d = p.model_dump()
    scenes = []
    for i, sc in enumerate(d["scenes"]):
        words = sum(len(ln["text"].split()) for ln in sc["dialogue"])
        scenes.append({"scene_id": f"SC-{i + 1:03d}", "location_id": sc["location"], "description": sc["description"],
                       "emotional_context": sc["emotional_context"], "characters": list(dict.fromkeys(sc["characters"])),
                       "events": [{"character_id": e["character"], "action": e["action"], "target": e["target"]}
                                  for e in sc["events"]],
                       "dialogue": [{"character_id": ln["character"], "text": ln["text"], "emotion": ln["emotion"]}
                                    for ln in sc["dialogue"]],
                       "estimated_duration_s": round(3.0 + sum(line_seconds(ln["text"]) + 0.8 for ln in sc["dialogue"])
                                                     if words else 3.0, 1)})
    total = round(sum(s["estimated_duration_s"] for s in scenes), 1)
    return ScriptSpecification(episode_id=ctx.episode_code, title=d["title"], synopsis=d["synopsis"], scenes=scenes,
                               estimated_duration_s=min(total, 1800.0))


def suggest_script(ctx: Context, params: GenerateScriptParams, llm: LLMSettings, semantic_repairs: int = 1) -> Suggestion:
    known_c, known_e = {c.code for c in ctx.cast}, {p.code for p in ctx.places}

    def convert(parsed):
        spec = script_to_spec(ctx, parsed)
        return spec, validate_script(spec, known_c, known_e)
    return _ask("generate_script", llm, params.provider, script_system(ctx), script_user(params), script_model(ctx),
                convert, semantic_repairs)


def run_generate_script(sessions: sessionmaker[Session], job_id: uuid.UUID, params_raw: dict[str, Any],
                        llm: LLMSettings, storage: Any, semantic_repairs: int = 1) -> dict[str, Any]:
    params = GenerateScriptParams.model_validate(params_raw)
    with sessions() as s:
        ctx = load_context(s, params.episode_id)
    record_in = {"params": params.model_dump(mode="json"), "cast": [c.code for c in ctx.cast],
                 "locations": [p.code for p in ctx.places]}
    try:
        out = suggest_script(ctx, params, llm, semantic_repairs)
    except AIRefused as exc:
        _refused(sessions, "generate_script", job_id, record_in, llm, params.provider, exc)
        raise
    return _finish(sessions, "generate_script", job_id, record_in, out, {"episode": ctx.episode_code})


# ================================================================ suggest_shots
def shots_model(ctx: Context) -> type[BaseModel]:
    script = ctx.script
    chars = _lit(script.characters())
    scenes = _lit(s.scene_id for s in script.scenes)
    marks = sorted({m for p in ctx.places for m in p.marks}) or ["centre"]
    cams = sorted({c for p in ctx.places for c in p.cameras}) or ["wide"]
    place = create_model("ProposedPlacement", __base__=_Strict, character=(chars, ...),
                         mark=(_lit(marks) | None, Field(default=None)))
    action = create_model("ProposedAction", __base__=_Strict, character=(chars, ...), action=(_lit(ACTIONS), ...),
                          start_s=(float, Field(default=0.0, ge=0, le=20)))
    shot = create_model(
        "ProposedShot", __base__=_Strict,
        scene=(scenes, ...), description=(str, Field(default="", max_length=300)),
        camera=(Literal["wide", "medium", "close"], "medium"),
        camera_mark=(_lit(cams) | None, Field(default=None, description="for wide shots in a location")),
        focus=(chars | None, Field(default=None)),
        movement=(Literal["static", "push_in", "pull_out", "pan_left", "pan_right"], "static"),
        placement=(list[place], Field(min_length=1, max_length=8)),  # type: ignore[valid-type]
        actions=(list[action], Field(default_factory=list, max_length=8)),  # type: ignore[valid-type]
        lines=(list[int], Field(default_factory=list, max_length=6,
                                description="indexes of the scene's dialogue lines spoken in this shot")),
        duration_s=(float, Field(default=3.0, ge=1.0, le=20.0)),
        transition=(Literal["cut", "fade", "dissolve"], "cut"),
    )
    return create_model("ShotsProposal", __base__=_Strict,
                        shots=(list[shot], Field(min_length=1, max_length=80)),  # type: ignore[valid-type]
                        notes=(str, Field(default="", max_length=600)))


def shots_system(ctx: Context) -> str:
    places = "\n".join(f"- {p.code}: {p.name}; standing marks {', '.join(p.marks) or 'none'}; camera marks "
                       f"{', '.join(p.cameras) or 'none'}" for p in ctx.places)
    return f"""You are the storyboard artist of the family 3D cartoon series "{ctx.series}".
Break the script into shots. Each shot belongs to one script scene, frames the characters in it and says
which of the scene's dialogue lines (by index, from 0) are spoken in it, in order; every line is spoken in
exactly one shot. Use the locations' marks for where characters stand and a camera mark for wide shots.

Locations:
{places or "- (none)"}

Rules:
- camera is wide, medium or close; focus is the character the camera frames (the speaker, usually).
- A shot is long enough for its lines (about 2.6 words per second) plus a beat: usually 2 to 8 seconds.
- Actions are one of: {", ".join(ACTIONS)}. Use a fade or dissolve only between scenes.
A person reviews and edits your storyboard before anything is produced."""


def shots_user(ctx: Context):
    def text(issues, previous):
        return "Script:\n" + ctx.script.model_dump_json(indent=1, exclude={"schema_version"}) + _repair_text(
            issues, previous)
    return text


def shots_to_spec(ctx: Context, p: BaseModel) -> tuple[StoryboardSpecification, list[Issue]]:
    script = ctx.script
    by_scene = {s.scene_id: s for s in script.scenes}
    used: dict[str, set[int]] = {}
    issues: list[Issue] = []
    shots = []
    for k, sh in enumerate(p.model_dump()["shots"]):
        sc = by_scene[sh["scene"]]
        lines, t = [], 0.3
        for idx in sh["lines"]:
            if not 0 <= idx < len(sc.dialogue):
                issues.append(Issue(path=f"shots[{k}].lines", code="no_such_line",
                                    message=f"{sc.scene_id} has lines 0..{len(sc.dialogue) - 1}"))
                continue
            if idx in used.setdefault(sc.scene_id, set()):
                issues.append(Issue(path=f"shots[{k}].lines", code="line_twice",
                                    message=f"{sc.scene_id} line {idx} is already spoken in another shot"))
                continue
            used[sc.scene_id].add(idx)
            ln = sc.dialogue[idx]
            lines.append({"character_id": ln.character_id, "text": ln.text, "start_s": round(t, 2),
                          "emotion": ln.emotion})
            t += line_seconds(ln.text) + 0.2
        speakers = {ln["character_id"]: ln["emotion"] for ln in lines if ln["emotion"]}
        place = [{"character_id": pl["character"], "mark": pl["mark"], "emotion": speakers.get(pl["character"])}
                 for pl in sh["placement"]]
        tr = {"kind": sh["transition"], "duration_s": 0.5 if sh["transition"] != "cut" else 0.0}
        shots.append({"shot_id": f"SH-{k + 1:03d}", "scene_id": sc.scene_id, "description": sh["description"],
                      "duration_s": round(max(sh["duration_s"], t + 0.4), 2), "environment": sc.location_id,
                      "camera": {"type": sh["camera"], "mark": sh["camera_mark"] if sc.location_id else None,
                                 "focus": sh["focus"], "movement": sh["movement"]},
                      "placement": place,
                      "actions": [{"character_id": a["character"], "action": a["action"], "start_s": a["start_s"]}
                                  for a in sh["actions"]],
                      "dialogue": lines, "transition_in": tr})
    for sc in script.scenes:
        missing = sorted(set(range(len(sc.dialogue))) - used.get(sc.scene_id, set()))
        if missing:
            issues.append(Issue(path="shots", code="line_missing",
                                message=f"{sc.scene_id} lines {missing} are not spoken in any shot"))
    return StoryboardSpecification(shots=shots), issues


def suggest_storyboard(ctx: Context, params: SuggestShotsParams, llm: LLMSettings,
                       semantic_repairs: int = 1) -> Suggestion:
    def convert(parsed):
        sb, issues = shots_to_spec(ctx, parsed)
        return sb, [*issues, *validate_storyboard(sb, ctx.script)]
    return _ask("suggest_shots", llm, params.provider, shots_system(ctx), shots_user(ctx), shots_model(ctx), convert,
                semantic_repairs)


def run_suggest_shots(sessions: sessionmaker[Session], job_id: uuid.UUID, params_raw: dict[str, Any],
                      llm: LLMSettings, storage: Any, semantic_repairs: int = 1) -> dict[str, Any]:
    params = SuggestShotsParams.model_validate(params_raw)
    with sessions() as s:
        ctx = load_context(s, params.episode_id, params.script_version_id)
    record_in = {"params": params.model_dump(mode="json"), "script_digest": ctx.script.digest()}
    try:
        out = suggest_storyboard(ctx, params, llm, semantic_repairs)
    except AIRefused as exc:
        _refused(sessions, "suggest_shots", job_id, record_in, llm, params.provider, exc)
        raise
    return _finish(sessions, "suggest_shots", job_id, record_in, out,
                   {"episode": ctx.episode_code, "script_version_id": str(params.script_version_id)})
