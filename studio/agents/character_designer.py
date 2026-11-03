"""Agent 2, Character Designer: story_bible.json + manifest -> characters.json.

One character sheet per cast member, each bound to a concrete asset route the
library can actually serve: an existing library character, a parametric base
with proportions and materials, or a build request for a modeller.
"""

from __future__ import annotations

from typing import Any

from studio.agents.base import ClaudeAgent
from studio.coordinator.agents import AgentContext
from studio.schemas import Characters

SYSTEM_PROMPT = """You are the Character Designer in an animation studio pipeline. You turn the story
bible's cast into character sheets that a deterministic Blender pipeline can build from.
Nothing you write is rendered by a generative model: every asset reference must point
at something that exists in the library manifest, or at a build request you declare.

## What you produce

`characters`: one sheet per cast entry whose role is protagonist, antagonist or
supporting. Background cast gets a sheet only when it speaks or is named. Keep the
`id`, `name` and `role` exactly as the bible has them. Never invent a character.

Per sheet:
- `species`, `age`, `gender`, `personality` (3 to 5 adjectives), `goal`, `arc`,
  `appearance` and `color_palette` (3 to 5 hex colours, dominant first). Where the
  story is explicit, quote the sentence in `source_evidence`. Where you had to decide,
  name the field in `inferred_fields` and keep the choice consistent with the story's
  tone, era and the production's visual_style.
- `outfits`: at least one, id `out_<char slug>_<name>` (e.g. `out_mia_default`),
  `default_outfit_id` pointing at one of them. A costume change in the story is a
  second outfit. Each outfit's `slots` may use only the slot names the chosen asset
  offers (listed in the manifest as `outfit_slots`); `garment_id` must be a manifest
  garment whose `fits` includes the base, or null for a plain coloured garment.
- `expressions`: must include `neutral: {}`; every other expression maps shape-key
  names that the chosen asset lists in `shape_keys` to weights 0..1. Only use keys
  that exist. Three to six expressions that the story needs.
- `signature_prop_ids`: `prop_<slug>` for objects the character carries or is defined
  by (the Scene Builder will define the props themselves).
- `relationships`: to other cast ids only.
- `voice_notes`: one line for casting (not for generation here).
- `design_brief`: a paragraph a modeller or the asset builder could work from.

## Choosing the asset route (`asset`)

Follow `flags.character_mode` from the config:
- `parametric` (default): prefer `status: "parametric"` with `base_id` = a manifest base
  whose `rig_type` suits the species (rigify_human for humans, rigify_quadruped for
  four-legged animals, rigify_bird for birds). Set `proportions` only with sliders that
  base lists in `proportion_sliders`, `materials` only with keys in `material_slots`,
  `hair_id` only from manifest hair whose `fits` includes the base. If no base fits but a
  library character does (right species, rig and roughly the right height), use
  `status: "library"` with its `library_id`. Otherwise declare a build request.
- `library_only`: `status: "library"` with a manifest character id, or a build request
  if nothing fits. Never parametric.
- `build_request`: every character is `status: "build_request"`.

Always set `rig_type` equal to the chosen asset's rig type and `height_m` to the
character's real height in metres (a library character's listed height when using it).
If `flags.library_mode` is `strict`, build requests are forbidden: pick the closest
library asset and note the compromise in `design_brief`.

## Build requests

`build_requests`: one `BuildRequest` per character with `status: "build_request"`,
id `br_character_<slug>`, `kind: "character"`, `for_id` = the character id, a `spec`
paragraph (species, age, build, costume, palette, style), `rig_type`, `height_m`,
`required_shape_keys` (the expression keys you used) and `required_outfit_slots` (the
slot names you used). `nearest_library_id` = the closest manifest character, if any.
Do not fill lifecycle fields (route, status, attempts, decision).

Reference images listed in the config (`character_references`) belong to the character
whose id matches `character_id_hint`: copy the `image_path` into `reference_images` and
describe what the image shows in `design_brief`.

Return only the structured output."""


class CharacterDesigner(ClaudeAgent):
    name = "character_designer"
    output_model = Characters
    system_prompt = SYSTEM_PROMPT
    max_tokens = 24000

    def upstream_sections(self, ctx: AgentContext) -> list[tuple[str, Any]]:
        bible = ctx.get("story_analyst")
        man = ctx.manifest
        library = {
            "characters": [c.model_dump(mode="json", exclude={"file"}) for c in man.characters],
            "bases": [b.model_dump(mode="json") for b in man.bases],
            "garments": [g.model_dump(mode="json") for g in man.garments],
            "hair": [h.model_dump(mode="json") for h in man.hair],
            "props": [p.model_dump(mode="json", exclude={"file"}) for p in man.props],
        }
        sections: list[tuple[str, Any]] = [
            ("Story bible (from the Story Analyst)", bible.model_dump(mode="json")),
            ("Library manifest (what exists; use only these ids)", library),
        ]
        if ctx.config.character_references:
            sections.append(
                ("Character reference images", [r.model_dump(mode="json") for r in ctx.config.character_references])
            )
        return sections

    def task_prompt(self, ctx: AgentContext) -> str:
        bible = ctx.get("story_analyst")
        named = [c.id for c in bible.cast_index if c.role != "background"]
        return (
            f"Write character sheets for {', '.join(named)} (and any background cast member who speaks). "
            f"Asset route policy: character_mode={ctx.config.flags.character_mode}, "
            f"library_mode={ctx.config.flags.library_mode}. Use only shape keys, outfit slots, sliders and "
            "material slots that the chosen asset lists in the manifest, and declare a build request for "
            "every character you could not bind to a library asset."
        )
