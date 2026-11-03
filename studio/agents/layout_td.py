"""Agent 5, Layout TD: direction + world + characters + manifest -> layout.json.

Deterministic where it can be, generative where it must be. Code fixes the
timeline (frame ranges per shot, contiguous per scene), the environment, the
library ids and the transitions. Claude places cameras, character paths, clips,
expression keys, attachments, lights and dialogue timing for one scene per call,
returning a draft without the code-owned fields; the agent assembles the final
``ShotLayout`` objects, so a bad draft fails schema validation and comes back as
feedback like any other agent error.
"""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import Field

from studio.agents.base import ClaudeAgent, scenes_to_redo
from studio.coordinator.agents import AgentContext
from studio.ids import SceneId, ShotId
from studio.schemas import Layout, StudioModel
from studio.schemas.build_requests import BuildRequest
from studio.schemas.layout import (
    Camera,
    ClipGap,
    DialogueTiming,
    EnvironmentRef,
    LayoutCharacter,
    LayoutProp,
    Light,
    ShotLayout,
)

SYSTEM_PROMPT = """You are the Layout TD in an animation studio pipeline, staging ONE scene for a
deterministic Blender build. Nothing is improvised at render time: the camera, every
character's path and animation clips, props, lights and dialogue timing you write are
executed exactly. Units are metres, Z up, the location origin on the ground at (0,0,0);
rotations in degrees; time in frames at the production fps. Frame numbers restart at 1
for each scene and the shots' frame ranges are given to you; use them exactly.

## Per shot

- `camera`: `rig` from the camera rig list; `keyframes` (1 for a static shot, 2 or more
  for a move, frames inside the shot's range) each with `position_m`, `look_at_m`
  (aim at the subject's chest/head: z about 0.75 x height for a standing human) and
  `focal_mm` from the style guide's lens set. Framing rule of thumb with a 36 mm sensor:
  distance_m = focal_mm x visible_height_m / (36 / aspect). Visible height by shot type:
  ELS 4 x H or more, LS 1.4 x H, MLS 1.0 x H, MS 0.6 x H, MCU 0.45 x H, CU 0.3 x H,
  ECU 0.15 x H, where H is the subject's height; OTS puts the camera 0.6 m behind and
  beside the listener's shoulder looking at the speaker. Keep every character listed in
  the shot inside the frame at every keyframe (they will be checked). Keep the camera
  inside the environment bounds and above the ground. `depth_of_field` optional.
- `characters`: exactly the characters the director put in the shot, each with
  `outfit_id` as the director has it, a `path` of keyframed positions (frame,
  position_m, facing_deg; from the shot's from_anchor to its to_anchor; a single point
  when standing still), `clips` from the clip list for this character's rig only:
  a locomotion clip covering the move (loop clips may run any length; a non-loop clip
  may not run longer than its frame count / speed), an idle or gesture clip otherwise,
  no gaps and no overlaps beyond `blend_frames`; walking speed from root_motion_m per
  clip cycle so the path and the clip agree (~1.1 m per cycle at speed 1 for a slow walk).
  `expression_keys` from the character's expression names at the frames the director's
  expression and dialogue call for (start the shot with one key). `look_at` targets
  (anchor id, character id or prop id) where eye lines matter. `attachments` for props
  the character holds (`bone` such as hand.R, hand.L).
- `props`: every prop in the shot. `placement` attached (held; no keyframes), static
  (one keyframe at an anchor or set-dressing position) or animated (2+ keyframes).
- `lights`: a key (sun outdoors, area or spot indoors), a fill if needed, and one light
  per prop that emits (type point, `parent_prop_id`, its colour and energy). Use a
  manifest `preset` name if one fits. Match the scene's colour script and time of day.
- `dialogue_timing`: one entry per director line (`line_id`, `speaker_id`,
  `frame_start`, `frame_end`): start where the director's start_sec says, length about
  2.5 s per 10 words, inside the shot, not overlapping.
- `clip_gaps`: when no clip in the list fits an action, use the closest clip anyway
  and record the gap (character, frame range, what was wanted) with a build request id.

## Build requests

`build_requests`: one per clip gap, id `br_clip_<slug>`, `kind: "clip"`, `for_id` the
character id, `spec` describing the motion, `rig_type`, `frames`, `loop`. Lifecycle
fields stay empty.

Return only the structured output."""


class ShotLayoutDraft(StudioModel):
    """ShotLayout without the code-owned fields (frames, scene, environment, transitions)."""

    shot_id: ShotId
    camera: Camera
    characters: list[LayoutCharacter] = Field(default_factory=list)
    props: list[LayoutProp] = Field(default_factory=list)
    lights: list[Light] = Field(default_factory=list)
    dialogue_timing: list[DialogueTiming] = Field(default_factory=list)
    clip_gaps: list[ClipGap] = Field(default_factory=list)


class SceneLayoutDraft(StudioModel):
    scene_id: SceneId
    shots: list[ShotLayoutDraft] = Field(min_length=1)
    build_requests: list[BuildRequest] = Field(default_factory=list)


def scene_timeline(direction_scene, fps: int) -> dict[str, tuple[int, int]]:
    """Shot id -> (frame_start, frame_end), contiguous from frame 1."""
    frames: dict[str, tuple[int, int]] = {}
    cursor = 1
    for shot in direction_scene.shots:
        n = max(1, round(shot.duration_sec * fps))
        frames[shot.id] = (cursor, cursor + n - 1)
        cursor += n
    return frames


class LayoutTD(ClaudeAgent):
    name = "layout_td"
    output_model = Layout
    system_prompt = SYSTEM_PROMPT
    max_tokens = 24000

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._previous: Layout | None = None

    # -------------------------------------------------------- code-owned
    @staticmethod
    def environment_for(location) -> EnvironmentRef:
        a = location.asset
        if a.status == "library" and a.library_id:
            return EnvironmentRef(library_id=a.library_id, time_variant=a.time_variant)
        # Composed or requested locations are not in the manifest yet; refer to the
        # closest thing the pipeline can load so the shot still validates in freeform.
        return EnvironmentRef(library_id=a.library_id or "lib_env_pending", time_variant=a.time_variant)

    def assemble(self, ctx: AgentContext, draft: SceneLayoutDraft) -> list[ShotLayout]:
        direction = ctx.get("director")
        world = ctx.get("scene_builder")
        chars = ctx.get("character_designer").by_id()
        props = world.prop_by_id()
        scene = world.scene_by_id()[draft.scene_id]
        loc = world.location_by_id()[scene.location_id]
        dscene = next(s for s in direction.scenes if s.scene_id == draft.scene_id)
        timeline = scene_timeline(dscene, ctx.config.fps)
        shots_by_id = {s.id: s for s in dscene.shots}
        env = self.environment_for(loc)
        out: list[ShotLayout] = []
        drafted = {d.shot_id: d for d in draft.shots}
        for shot in dscene.shots:
            d = drafted.get(shot.id)
            if d is None:
                raise ValueError(f"{draft.scene_id}: no layout drafted for {shot.id}")
            lo, hi = timeline[shot.id]
            characters = []
            for lc in d.characters:
                ch = chars.get(lc.character_id)
                lib = ch.asset.library_id if ch and ch.asset.status == "library" else (lc.library_id if ch is None else None)
                characters.append(lc.model_copy(update={"library_id": lib}))
            layout_props = [
                lp.model_copy(update={"library_id": props[lp.prop_id].asset.library_id if lp.prop_id in props else lp.library_id})
                for lp in d.props
            ]
            out.append(
                ShotLayout(
                    shot_id=shot.id,
                    scene_id=draft.scene_id,
                    frame_start=lo,
                    frame_end=hi,
                    environment=env,
                    camera=d.camera,
                    characters=characters,
                    props=layout_props,
                    lights=d.lights,
                    dialogue_timing=d.dialogue_timing,
                    transition_in=shot.transition_in,
                    transition_out=shot.transition_out,
                    clip_gaps=d.clip_gaps,
                )
            )
        extra = set(drafted) - set(shots_by_id)
        if extra:
            raise ValueError(f"{draft.scene_id}: layout drafted for shots not in the direction: {sorted(extra)}")
        return out

    # ------------------------------------------------------------ prompt
    def scene_message(self, ctx: AgentContext, scene_id: str, feedback) -> str:
        cfg = ctx.config
        direction = ctx.get("director")
        world = ctx.get("scene_builder")
        chars = ctx.get("character_designer").by_id()
        man = ctx.manifest
        scene = world.scene_by_id()[scene_id]
        loc = world.location_by_id()[scene.location_id]
        dscene = next(s for s in direction.scenes if s.scene_id == scene_id)
        timeline = scene_timeline(dscene, cfg.fps)
        env = man.environment_by_id().get(loc.asset.library_id or "")
        w, h = cfg.resolution
        aspect = w / h

        shots = []
        for shot in dscene.shots:
            lo, hi = timeline[shot.id]
            s = shot.model_dump(mode="json")
            s["frame_start"], s["frame_end"] = lo, hi
            for d in s["dialogue"]:
                d["suggested_frame_start"] = lo + round(d["start_sec"] * cfg.fps)
                d["suggested_frames"] = max(12, round(len(d["text"].split()) / 10 * 2.5 * cfg.fps))
            shots.append(s)

        cast_ids = {c.character_id for sh in dscene.shots for c in sh.characters}
        cast = []
        for cid in sorted(cast_ids):
            ch = chars[cid]
            rig = ch.asset.rig_type
            cast.append(
                {
                    "id": cid,
                    "height_m": ch.asset.height_m,
                    "rig_type": rig,
                    "expressions": list(ch.expressions),
                    "signature_prop_ids": ch.signature_prop_ids,
                    "clips": [c.model_dump(mode="json", exclude={"source", "licence"}) for c in man.clips if c.rig_type == rig],
                }
            )
        prop_ids = {p for sh in dscene.shots for p in sh.prop_ids} | {p.id for p in world.props if p.owner_id in cast_ids}
        props = [p.model_dump(mode="json") for p in world.props if p.id in prop_ids]
        anchors = [a.model_dump(mode="json") for a in loc.anchors]
        color = next((c.model_dump(mode="json") for c in direction.style_guide.color_script if c.scene_id == scene_id), None)

        parts = [
            "## Production\n" + json.dumps({"fps": cfg.fps, "aspect_ratio": cfg.aspect_ratio, "aspect": round(aspect, 4), "resolution": cfg.resolution, "sensor_mm": 36, "lens_set_mm": direction.style_guide.lens_set_mm, "camera_rigs": man.camera_rigs, "lighting_presets": man.lighting_presets}, indent=2),
            f"## Scene {scene_id}\n" + json.dumps({"title": scene.title, "time_of_day": scene.time_of_day, "weather": scene.weather, "mood": scene.mood, "color_script": color}, indent=2),
            "## Environment\n" + json.dumps({"location_id": loc.id, "name": loc.name, "description": loc.description, "asset": loc.asset.model_dump(mode="json"), "bounds_m": env.bounds_m if env else loc.asset.bounds_m, "anchors": anchors, "set_dressing": [s.model_dump(mode="json") for s in loc.set_dressing]}, indent=2),
            "## Cast in this scene (with the clips available for each rig)\n" + json.dumps(cast, indent=2),
            "## Props\n" + json.dumps(props, indent=2),
            "## Shots to lay out (frame ranges are fixed; use them exactly)\n" + json.dumps(shots, indent=2),
        ]
        if feedback:
            lines = "\n".join(f"- [{i.category}] {i.message}" + (f" Fix: {i.suggested_fix}" if i.suggested_fix else "") for i in feedback)
            parts.append("## Problems with your previous layout for this scene\nFix every one and return the complete scene again:\n" + lines)
        if ctx.revise_notes:
            parts.append("## Revision notes from the human reviewer\n" + ctx.revise_notes)
        parts.append(
            f"## Task\nLay out {scene_id}: one draft per shot above, `scene_id` \"{scene_id}\", cameras that keep every "
            "listed character in frame, paths between the director's anchors, clips for each character's rig only, "
            "dialogue timing for every line, and build requests for any clip gaps."
        )
        return "\n\n".join(parts)

    # --------------------------------------------------------------- run
    def run(self, ctx: AgentContext) -> StudioModel:
        self.last_usage = {}
        direction = ctx.get("director")
        world = ctx.get("scene_builder")
        scene_ids = [s.scene_id for s in direction.scenes]
        previous = self._previous if (self._previous is not None and ctx.feedback and not ctx.revise_notes) else None
        redo, per_scene = scenes_to_redo([] if ctx.revise_notes else ctx.feedback, scene_ids, world)
        kept: dict[str, list[ShotLayout]] = {}
        kept_requests: list[BuildRequest] = []
        if previous is None:
            redo = set(scene_ids)  # nothing to keep: everyone hears the feedback, everything is redone
        else:
            for sl in previous.shots:
                kept.setdefault(sl.scene_id, []).append(sl)
            redo |= {sid for sid in scene_ids if sid not in kept}
            redone_cast = {c for sid in redo for c in _cast_of(direction, sid)}
            kept_requests = [br for br in previous.build_requests if br.for_id not in redone_cast]

        def do_scene(sid: str) -> tuple[list[ShotLayout], list[BuildRequest]]:
            draft = self.call(self.scene_message(ctx, sid, per_scene.get(sid, [])), SceneLayoutDraft)
            assert isinstance(draft, SceneLayoutDraft)
            return self.assemble(ctx, draft), list(draft.build_requests)

        todo = [sid for sid in scene_ids if sid in redo]
        results: dict[str, list[ShotLayout]] = {sid: shots for sid, shots in kept.items() if sid not in redo}
        requests: list[BuildRequest] = list(kept_requests)
        if ctx.config.flags.parallel_calls and len(todo) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(todo))) as pool:
                for sid, (shots, brs) in zip(todo, pool.map(do_scene, todo)):
                    results[sid], requests = shots, requests + brs
        else:
            for sid in todo:
                shots, brs = do_scene(sid)
                results[sid], requests = shots, requests + brs

        seen: set[str] = set()
        unique = [br for br in requests if not (br.id in seen or seen.add(br.id))]
        layout = Layout(fps=ctx.config.fps, shots=[s for sid in scene_ids for s in results[sid]], build_requests=unique)
        self._previous = layout
        return layout


def _cast_of(direction, scene_id: str) -> set[str]:
    sd = next(s for s in direction.scenes if s.scene_id == scene_id)
    return {c.character_id for sh in sd.shots for c in sh.characters}
