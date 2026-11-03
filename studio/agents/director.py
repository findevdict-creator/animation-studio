"""Agent 4, Director: bible + characters + world -> direction.json.

Two kinds of call: one for the style guide (pacing, lens set, colour script over
all scenes), then one per scene for its shot list. Scenes are independent given
the style guide and the previous scene's closing shot, so they run in parallel
when ``flags.parallel_calls`` is on. On a retry only the scenes that have
feedback are redone; the others are kept from the previous attempt.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from studio.agents.base import ClaudeAgent, scenes_to_redo
from studio.coordinator.agents import AgentContext
from studio.schemas import Direction, StudioModel
from studio.schemas.continuity import Issue
from studio.schemas.direction import SceneDirection, StyleGuide

STYLE_PROMPT = """You are the Director in an animation studio pipeline, setting the visual approach
before any shot is planned. From the story bible, the character sheets and the world
you write the style guide: `visual_style` (expand the production's visual_style into a
paragraph of concrete choices: shading, line, texture, colour behaviour), `aspect_ratio`
and `fps` copied from the config, `pacing` (how cutting rhythm changes across the film),
`lens_set_mm` (3 to 5 focal lengths every shot will use; wider than 24 or longer than
135 is unusual), and a `color_script` with one entry per scene in order: its
`scene_id`, 3 to 5 `dominant_colors` as hex, and a one-sentence `lighting` description
tied to the scene's time of day and mood. Keep colour continuity between adjacent
scenes unless the story turns.

Return only the structured output."""

SCENE_PROMPT = """You are the Director in an animation studio pipeline, planning the shots of ONE scene.
Your shot list is executed by a deterministic layout and Blender pipeline, so every
reference must resolve: characters to the scene cast, outfits to that character's
outfits, expressions to that character's expression names, anchors to the scene's
location, props to the world's props, beats to this scene's beats.

## Shots

- Ids `sh_<scene number>_<shot number>`, e.g. `sh_03_01`, `sh_03_02`, `order` from 1.
- Each shot covers one `beat_id` from this scene. Cover every beat of the scene; a
  beat usually needs 1 to 4 shots. Vary shot size and angle for coverage: establish
  the space (LS or ELS) when a scene opens in a new place, go closer (MS, MCU, CU) for
  dialogue and reactions, use OTS for conversations, INSERT for props that matter.
- `duration_sec`: 1.5 to 12 seconds per shot. The shots must sum to the scene's
  `duration_sec` within 0.5 s, and `duration_sec` equals the planned scene duration from
  the world file.
- `subject_id`: the character the camera is framed on (must be in `characters`).
- `characters`: everyone visible in the shot. `from_anchor_id` and `to_anchor_id` give
  where they are at the start and end of the shot (same anchor or null when they do not
  move; a move between anchors is a walk the layout will animate). `expression` is one
  of that character's expression names. `action` is a sentence; `action_tags` are from
  the clip vocabulary given below so the layout can pick animation clips (e.g.
  ["locomotion", "slow"], ["gesture", "point"], ["idle"]). Keep a character's position
  continuous: where they end one shot is where they start the next.
- `dialogue`: every verbatim line of the beat appears in exactly one shot of that beat,
  with the exact text from the bible, `speaker_id` in `characters`, `line_id`
  `ln_<scene>_<shot>_<n>` (e.g. `ln_03_02_01`), `emotion`, `delivery` and `start_sec`
  within the shot (allow about 2.5 s per 10 words, lines do not overlap). Adapted and
  added lines from the bible may be used or dropped.
- `camera_movement` static unless a move serves the beat; `composition` and `lighting`
  one sentence each, consistent with the style guide's colour script for this scene.
- `transition_in` of the first shot and `transition_out` of the last shot describe the
  scene boundary (fade_in only for the film's first scene, fade_out only for the last);
  within a scene transitions are cuts.

Return only the structured output."""


class Director(ClaudeAgent):
    name = "director"
    output_model = Direction
    system_prompt = SCENE_PROMPT
    max_tokens = 16000

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._previous: Direction | None = None

    # ------------------------------------------------------------ context
    def _common(self, ctx: AgentContext) -> dict[str, Any]:
        bible = ctx.get("story_analyst")
        chars = ctx.get("character_designer")
        world = ctx.get("scene_builder")
        cfg = ctx.config
        return {
            "config": {
                "target_duration_sec": cfg.target_duration_sec,
                "fps": cfg.fps,
                "visual_style": cfg.visual_style,
                "aspect_ratio": cfg.aspect_ratio,
                "audience": cfg.audience,
                "language": cfg.language,
                "strict_dialogue": cfg.flags.strict_dialogue,
            },
            "bible": bible,
            "chars": chars,
            "world": world,
            "cast_summary": [
                {
                    "id": c.id,
                    "name": c.name,
                    "role": c.role,
                    "personality": c.personality,
                    "outfits": [o.id for o in c.outfits],
                    "expressions": list(c.expressions),
                    "voice_notes": c.voice_notes,
                }
                for c in chars.characters
            ],
            "clip_tags": sorted({t for clip in ctx.manifest.clips for t in clip.tags}),
        }

    def style_message(self, ctx: AgentContext, common: dict[str, Any]) -> str:
        bible, world = common["bible"], common["world"]
        scenes = [
            {
                "id": s.id, "title": s.title, "location_id": s.location_id, "time_of_day": s.time_of_day,
                "mood": s.mood, "estimated_duration_sec": s.estimated_duration_sec,
                "beats": [b.summary for b in bible.beats if b.id in s.beat_ids],
            }
            for s in world.scenes
        ]
        parts = [
            "## Production config\n" + json.dumps(common["config"], indent=2),
            "## Story\n" + f"{bible.title}: {bible.logline}\n\n{bible.synopsis}\n\nTone: {bible.tone}. Themes: {', '.join(bible.themes)}.",
            "## Cast\n" + json.dumps(common["cast_summary"], indent=2),
            "## Locations\n" + json.dumps([{"id": l.id, "name": l.name, "setting_type": l.setting_type, "description": l.description, "color_palette": l.color_palette} for l in world.locations], indent=2),
            "## Scenes (in order)\n" + json.dumps(scenes, indent=2),
        ]
        if ctx.revise_notes:
            parts.append("## Revision notes from the human reviewer\n" + ctx.revise_notes)
        parts.append("## Task\nWrite the style guide with a color_script entry for every scene above, in order.")
        return "\n\n".join(parts)

    def scene_message(
        self,
        ctx: AgentContext,
        common: dict[str, Any],
        scene_id: str,
        style: StyleGuide,
        previous_shot: dict[str, Any] | None,
        feedback: list[Issue],
        is_first: bool,
        is_last: bool,
    ) -> str:
        bible, chars, world = common["bible"], common["chars"], common["world"]
        scene = world.scene_by_id()[scene_id]
        loc = world.location_by_id()[scene.location_id]
        beats = [b.model_dump(mode="json") for b in bible.beats if b.id in scene.beat_ids]
        cast = [
            {
                **next(c for c in common["cast_summary"] if c["id"] == m.character_id),
                "scene_outfit_id": m.outfit_id,
                "starting_emotion": m.starting_emotion,
                "start_anchor_id": m.start_anchor_id,
            }
            for m in scene.cast
        ]
        props = [p.model_dump(mode="json", include={"id", "name", "description", "owner_id", "emits_light"}) for p in world.props if p.id in scene.prop_ids or p.owner_id in scene.cast_ids()]
        color = next((c.model_dump(mode="json") for c in style.color_script if c.scene_id == scene_id), None)
        parts = [
            "## Production config\n" + json.dumps(common["config"], indent=2),
            "## Style guide\n" + json.dumps(style.model_dump(mode="json", exclude={"color_script"}), indent=2)
            + ("\n\nColour script for this scene:\n" + json.dumps(color, indent=2) if color else ""),
            f"## Scene {scene_id} (scene {scene.order} of {len(world.scenes)}"
            + (", the film's first" if is_first else "") + (", the film's last" if is_last else "") + ")\n"
            + json.dumps(scene.model_dump(mode="json", exclude={"cast", "prop_ids"}), indent=2),
            "## Beats in this scene (cover each; dialogue text is verbatim)\n" + json.dumps(beats, indent=2),
            "## Cast in this scene\n" + json.dumps(cast, indent=2),
            "## Location and its anchors\n" + json.dumps({"id": loc.id, "name": loc.name, "description": loc.description, "key_features": loc.key_features, "anchors": [a.model_dump(mode="json") for a in loc.anchors], "set_dressing": [s.model_dump(mode="json") for s in loc.set_dressing]}, indent=2),
            "## Props available\n" + json.dumps(props, indent=2),
            "## Clip vocabulary for action_tags\n" + ", ".join(common["clip_tags"]),
        ]
        if previous_shot:
            parts.append("## Closing shot of the previous scene (for continuity of positions and transitions)\n" + json.dumps(previous_shot, indent=2))
        if feedback:
            lines = "\n".join(f"- [{i.category}] {i.message}" + (f" Fix: {i.suggested_fix}" if i.suggested_fix else "") for i in feedback)
            parts.append("## Problems with your previous shot list for this scene\nFix every one and return the complete scene again:\n" + lines)
        if ctx.revise_notes:
            parts.append("## Revision notes from the human reviewer\n" + ctx.revise_notes)
        parts.append(
            f"## Task\nWrite the shot list for {scene_id}: `scene_id` \"{scene_id}\", `duration_sec` "
            f"{scene.estimated_duration_sec:g}, shots summing to it, covering beats {scene.beat_ids}, "
            "with every verbatim line placed and every id resolving to the lists above."
        )
        return "\n\n".join(parts)

    # ---------------------------------------------------------------- run
    def run(self, ctx: AgentContext) -> StudioModel:
        self.last_usage = {}
        common = self._common(ctx)
        world = common["world"]
        scene_ids = [s.id for s in world.scenes]

        # Which scenes to (re)do. With nothing kept from a previous attempt every scene
        # is redone, but the feedback still reaches the scenes it belongs to.
        previous_scenes: dict[str, SceneDirection] = {}
        redo, per_scene = scenes_to_redo([] if ctx.revise_notes else ctx.feedback, scene_ids, world)
        if self._previous is not None and ctx.feedback and not ctx.revise_notes:
            previous_scenes = {s.scene_id: s for s in self._previous.scenes}
            redo |= {sid for sid in scene_ids if sid not in previous_scenes}
        else:
            redo = set(scene_ids)

        style = self._previous.style_guide if (self._previous is not None and ctx.feedback and not ctx.revise_notes) else None
        if style is None:
            style = self.call(self.style_message(ctx, common), StyleGuide)
        assert isinstance(style, StyleGuide)

        def closing_shot(index: int) -> dict[str, Any] | None:
            if index == 0:
                return None
            prev = previous_scenes.get(scene_ids[index - 1])
            if prev is None:
                return None
            shot = prev.shots[-1]
            return shot.model_dump(mode="json", include={"id", "shot_type", "characters", "transition_out"})

        def do_scene(index: int) -> SceneDirection:
            sid = scene_ids[index]
            msg = self.scene_message(
                ctx, common, sid, style, closing_shot(index), per_scene[sid],
                is_first=index == 0, is_last=index == len(scene_ids) - 1,
            )
            result = self.call(msg, SceneDirection)
            assert isinstance(result, SceneDirection)
            return result

        todo = [i for i, sid in enumerate(scene_ids) if sid in redo]
        results: dict[str, SceneDirection] = dict(previous_scenes)
        if ctx.config.flags.parallel_calls and len(todo) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(todo))) as pool:
                for index, sd in zip(todo, pool.map(do_scene, todo)):
                    results[scene_ids[index]] = sd
        else:
            for index in todo:
                results[scene_ids[index]] = do_scene(index)

        direction = Direction(style_guide=style, scenes=[results[sid] for sid in scene_ids])
        self._previous = direction
        return direction
