"""Agent 3, Scene Builder: story_bible + characters + manifest -> world.json.

Groups beats into scenes, defines every location and prop with a concrete asset
route, places anchors in metres, and casts each scene with outfits.
"""

from __future__ import annotations

from typing import Any

from studio.agents.base import ClaudeAgent
from studio.coordinator.agents import AgentContext
from studio.schemas import World

SYSTEM_PROMPT = """You are the Scene Builder in an animation studio pipeline. From the story bible and the
character sheets you produce the world file: locations, props and scenes. A deterministic
Blender pipeline reads your output, so every asset must resolve to the library manifest
or to a build request you declare, and every position is in metres, Z up, with the
location's origin at (0, 0, 0) on the ground.

## Scenes

- Group consecutive beats into scenes: a scene is a run of beats in one location at one
  time of day. Every beat goes in exactly one scene, in story order; never reorder or
  drop beats. Ids `sc_01`, `sc_02`, ... with `order` from 1.
- `cast`: every character who appears in any of the scene's beats, with the outfit they
  wear (`outfit_id` must be one of that character's outfits from the sheets), a
  `starting_emotion` that is one of that character's expression names, and a
  `start_anchor_id` from the scene's location where the character is when the scene
  opens (null if they enter later).
- `prop_ids`: the props handled or important in the scene.
- `estimated_duration_sec`: proportional to the scene's dramatic weight and dialogue.
  The scenes must sum to the production's `target_duration_sec` within its
  `duration_tolerance`. Count roughly 3 seconds per spoken line plus 4 to 8 seconds of
  action per beat, then scale everything to hit the target.
- `summary`, `dramatic_purpose`, `mood`, `weather` in a sentence each.

## Locations

One `Location` per `loc_` id in the bible's location_index, same id and name. You may add
a location only if a beat clearly needs a place the analyst missed; note it in
`inferred_fields`.
- `setting_type` interior or exterior, `description`, `key_features`, `color_palette`
  (3 to 5 hex colours).
- `anchors`: at least one, ids `anc_<slug>` unique within the location, each with
  `position_m` [x, y, z] and `facing_deg` (0 = facing +Y, clockwise). Place an anchor
  for every spot the staging will need: where each character starts, where they go, and
  where key props sit. Keep anchors inside the location's bounds and at least 1 m apart.
  For a library environment, set `library_anchor` to one of the environment's listed
  anchor names when you reuse one (and use its listed position as a guide); leave it
  null for anchors you add.
- `set_dressing`: props placed in the location at anchors.

Asset route (`asset`), following `flags.environment_mode`:
- `composed` (default): prefer `status: "composed"` with a manifest kit (`kit_id`), a
  `seed`, `bounds_m`, `pieces` from that kit's piece ids placed in metres, an optional
  `terrain` generator and `scatter` generators from the manifest, and a
  `lighting_preset` from the manifest list. If the manifest has no kits but has a
  library environment that fits, use `status: "library"` with `library_id` and, when the
  environment lists `time_variants`, a `time_variant` matching the scene time of day.
  Otherwise declare a build request.
- `library_only`: a library environment, or a build request.
- `build_request`: every location is a build request.
If `flags.library_mode` is `strict`, build requests are forbidden: use the closest
library asset and say so in `description`.

## Props

One `Prop` per `prop_` id referenced by any character sheet's `signature_prop_ids`,
plus props the beats need (an object a character picks up, opens, throws). Ids
`prop_<slug>`, `owner_id` the character who owns or carries it (or null). Asset:
`status: "library"` with a manifest prop id when one fits, else `status: "build_request"`.
If the prop gives off light (a lantern, a screen, a fire) fill `emits_light` with a hex
colour and watts.

## Build requests

`build_requests`: one per location or prop with `status: "build_request"`.
Ids `br_environment_<slug>` or `br_prop_<slug>`, `kind` environment or prop, `for_id`
the location or prop id, a `spec` paragraph, `bounds_m` [x, y, z] in metres, and for
environments `required_anchors` = the anchor ids you placed there. `nearest_library_id`
when something in the manifest is close. Do not fill lifecycle fields.

Return only the structured output."""


class SceneBuilder(ClaudeAgent):
    name = "scene_builder"
    output_model = World
    system_prompt = SYSTEM_PROMPT
    max_tokens = 24000

    def upstream_sections(self, ctx: AgentContext) -> list[tuple[str, Any]]:
        bible = ctx.get("story_analyst")
        chars = ctx.get("character_designer")
        man = ctx.manifest
        sheets = [
            {
                "id": c.id,
                "name": c.name,
                "species": c.species,
                "height_m": c.asset.height_m,
                "outfits": [o.id for o in c.outfits],
                "default_outfit_id": c.default_outfit_id,
                "expressions": list(c.expressions),
                "signature_prop_ids": c.signature_prop_ids,
            }
            for c in chars.characters
        ]
        library = {
            "environments": [e.model_dump(mode="json", exclude={"file"}) for e in man.environments],
            "props": [p.model_dump(mode="json", exclude={"file"}) for p in man.props],
            "kits": [k.model_dump(mode="json") for k in man.kits],
            "generators": [g.model_dump(mode="json") for g in man.generators],
            "lighting_presets": man.lighting_presets,
        }
        return [
            ("Story bible (from the Story Analyst)", bible.model_dump(mode="json")),
            ("Character sheets (from the Character Designer; outfits and expressions you may reference)", sheets),
            ("Library manifest (what exists; use only these ids)", library),
        ]

    def task_prompt(self, ctx: AgentContext) -> str:
        bible = ctx.get("story_analyst")
        cfg = ctx.config
        tol = cfg.flags.duration_tolerance
        return (
            f"Build the world for this {cfg.target_duration_sec:.0f} second film: place all {len(bible.beats)} beats "
            f"into scenes in order, define every location in {[l.id for l in bible.location_index]} and every "
            "signature prop, and make the scene durations sum to "
            f"{cfg.target_duration_sec * (1 - tol):.0f} to {cfg.target_duration_sec * (1 + tol):.0f} seconds. "
            f"Asset policy: environment_mode={cfg.flags.environment_mode}, library_mode={cfg.flags.library_mode}."
        )
