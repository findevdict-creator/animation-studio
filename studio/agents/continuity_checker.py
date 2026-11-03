"""Agent 6, Continuity Checker: every upstream file -> continuity_report.json.

The code hard checks already catch broken references, durations, bounds and
timelines; the coordinator merges their report with this one. This agent reads
the whole package for the problems only a reader notices: story logic, position
and prop continuity across shots, costume and expression sense, dialogue that
contradicts the bible, pacing that fights the direction. It never rewrites
anything; it reports, with an owner agent so the fix loop can route each error.
"""

from __future__ import annotations

import json
from typing import Any

from studio.agents.base import ClaudeAgent
from studio.coordinator.agents import AgentContext
from studio.schemas import ContinuityReport

SYSTEM_PROMPT = """You are the Continuity Checker in an animation studio pipeline: the last reader before
the package goes to the Blender build. You get the story bible, the character sheets, the
world, the shot list and the layout, plus the report of the automated checks that already
ran. Find what those checks cannot: problems of meaning and continuity.

## Look for

- Story logic: a beat's content missing from its shots; events out of order; a character
  present in a shot who is not in the beat (or absent when the beat needs them); a
  character reacting to something they could not have seen or heard.
- Position continuity: where a character ends one shot is where they start the next
  within a scene (compare path end and start positions and anchors); a character who
  must walk a distance has the frames and the locomotion clip to do it.
- Props: a prop held in one shot is not lying elsewhere in the next; a prop appears
  before the story introduces it; a light-emitting prop has a light when it is lit.
- Costume and expression: outfit changes only where the story has one; expressions and
  dialogue emotion match the beat's tone; eye lines point at what is spoken to.
- Dialogue: every verbatim line is in the right beat with the right speaker; adapted or
  added lines keep the story's meaning and the character's voice; timings leave room to
  hear the line (about 2.5 s per 10 words).
- Direction and layout sense: shot sizes and lenses that match what the shot says it
  shows; a camera move described but not keyframed; a scene opening on a new location
  without any establishing view; lighting at odds with time of day or colour script.
- Audience and tone against the production config.

## How to report

- `issues`: one entry per distinct problem. `severity` is `error` only when the film
  would be wrong or unbuildable as written and the fix is clear; everything else is a
  `warning`. Each error costs a fix round, so do not raise style preferences to errors.
- `category` from the list; `where` is the id of the thing (shot, scene, beat, character,
  prop id) so the fix can be routed: use a shot id for shot-level problems and a beat id
  for story-level ones. `entity_ids` lists the ids involved.
- `owner_agent`: who should fix it. story_analyst for bible content, character_designer
  for sheets, scene_builder for scenes, locations and props, director for shots and
  dialogue placement, layout_td for cameras, paths, clips, lights and timing.
- `message` states the problem in one or two sentences; `suggested_fix` says the change.
- `id`s `iss_001`, `iss_002`, ... in order of severity then shot order.
- Do not repeat anything the automated report already lists.
- `status` is `fail` if there is any error, else `pass`. `open_build_requests`: leave as
  the automated report has it.

Return only the structured output."""


def _compact_layout(layout) -> list[dict[str, Any]]:
    shots = []
    for sl in layout.shots:
        shots.append(
            {
                "shot_id": sl.shot_id,
                "frames": [sl.frame_start, sl.frame_end],
                "camera": [
                    {"frame": k.frame, "position_m": k.position_m, "look_at_m": k.look_at_m, "focal_mm": k.focal_mm}
                    for k in sl.camera.keyframes
                ],
                "characters": [
                    {
                        "id": c.character_id,
                        "outfit_id": c.outfit_id,
                        "path_start": {"frame": c.path[0].frame, "position_m": c.path[0].position_m},
                        "path_end": {"frame": c.path[-1].frame, "position_m": c.path[-1].position_m},
                        "clips": [f"{u.clip_id} {u.frame_start}-{u.frame_end}" for u in c.clips],
                        "expressions": [f"{e.expression}@{e.frame}" for e in c.expression_keys],
                        "look_at": [f"{l.target}@{l.frame}" for l in c.look_at],
                        "holding": [a.prop_id for a in c.attachments],
                    }
                    for c in sl.characters
                ],
                "props": [{"id": p.prop_id, "placement": p.placement, "at": p.keyframes[0].position_m if p.keyframes else None} for p in sl.props],
                "lights": [f"{l.type} {l.id} {l.color} {l.energy}" + (f" on {l.parent_prop_id}" if l.parent_prop_id else "") for l in sl.lights],
                "dialogue_timing": [f"{d.line_id} {d.frame_start}-{d.frame_end}" for d in sl.dialogue_timing],
                "clip_gaps": [g.model_dump(mode="json") for g in sl.clip_gaps],
            }
        )
    return shots


class ContinuityChecker(ClaudeAgent):
    name = "continuity_checker"
    output_model = ContinuityReport
    system_prompt = SYSTEM_PROMPT
    max_tokens = 16000

    def upstream_sections(self, ctx: AgentContext) -> list[tuple[str, Any]]:
        sections: list[tuple[str, Any]] = [
            ("Story bible", ctx.get("story_analyst").model_dump(mode="json")),
            (
                "Character sheets (condensed)",
                [
                    c.model_dump(mode="json", include={"id", "name", "role", "species", "age", "personality", "goal", "arc", "default_outfit_id", "signature_prop_ids", "relationships"})
                    | {"outfits": [o.id + (": " + o.description if o.description else "") for o in c.outfits], "expressions": list(c.expressions), "height_m": c.asset.height_m}
                    for c in ctx.get("character_designer").characters
                ],
            ),
            ("World: scenes, locations, props", ctx.get("scene_builder").model_dump(mode="json", exclude={"build_requests"})),
            ("Direction: style guide and shots", ctx.get("director").model_dump(mode="json")),
        ]
        if "layout_td" in ctx.upstream:
            sections.append(("Layout (condensed per shot; metres and frames)", _compact_layout(ctx.upstream["layout_td"])))
        code = getattr(ctx, "code_report", None)
        if code is not None:
            sections.append(("Automated checks already reported (do not repeat these)", code.model_dump(mode="json")))
        return sections

    def task_prompt(self, ctx: AgentContext) -> str:
        return (
            "Review the whole package for continuity and story logic. Report only real problems, errors only where the "
            "film would be wrong as written, each with the owner agent and a suggested fix, and set status accordingly."
        )
