# Animation Studio

Story text in, a validated production package out. Six text agents (Story Analyst, Character
Designer, World and Scene Builder, Director, Layout TD, Continuity Checker) run under a code
coordinator. Rendering is deterministic Blender code in a later stage; no generative model is in
the render path.

## Modules

Each module lives on its own numbered branch, built on the branch before it, so a module can be
revisited on its branch and merged back into `main`.

| Branch | Module | What it adds |
| --- | --- | --- |
| `01-schemas` | `studio/schemas`, `studio/checks.py` | Pydantic models for every pipeline file, ID formats, cross-reference checks |
| `02-coordinator` | `studio/coordinator` | Runs agents in order, validates, retries, approval gates, flags, merge, exports |
| `03-library-scanner` | `tools/` | Blender headless scripts: scan the asset library into `manifest.json`, lint, turntables |
| `04-story-analyst` | `studio/agents/story_analyst.py` | First agent on the Claude API with structured output |
| `05-character-designer` | `studio/agents/character_designer.py` | Character sheets bound to library, parametric or build-request assets |
| `06-scene-builder` | `studio/agents/scene_builder.py` | Beats into scenes, locations with anchors, props, build requests |
| `07-director` | `studio/agents/director.py` | Style guide, then a shot list per scene (parallel, partial retries) |
| `08-layout-td` | `studio/agents/layout_td.py` | Per-scene staging drafts; code owns frames, environment, library ids |
| `09-continuity-checker` | `studio/agents/continuity_checker.py` | Reads the whole package for story and continuity problems; routed to owner agents |
| `10-build-character` | `tools/build_character.py`, `studio/assets` | Asset build phase 1: parametric bases, wardrobe, hair; sheets become rigged `.blend` files |
| `11-compose-env` | `tools/compose_env.py`, `studio/assets/environments.py` | Asset build phase 3: kits, seeded terrain and scatter; compositions become environment `.blend` files |
| `12-clip-pipeline` | `tools/retarget.py`, `tools/tag_clips.py`, `tools/procedural.py`, `studio/assets/clips.py` | Asset build phase 2: mocap retargeting to base rigs, tagging, procedural layers, clip search and transitions |
| `13-asset-builder` | `tools/asset_builder/`, `studio/agents/asset_builder.py`, `studio/agents/render_qa.py`, `studio/assets/builder.py` | Asset build phase 4: an agent writes bpy scripts for prop requests, a sandbox runs them, Render QA scores the turntable, accepted scripts join the generator library |
| `14-build-queue` | `studio/assets/queue.py` | Asset build phase 5: build requests ranked by screen time, routed variant → generator → asset_builder → image_to_3d → human, deduplicated, with per-story metrics |

## Setup

```
pip install -e ".[dev]"
pytest
```

## Running

```
# dry run with canned agents, no API key needed
studio run --story tests/fixtures/lantern_fox/story.txt --run-dir runs/demo --agents stub

# the real agents (needs ANTHROPIC_API_KEY; STUDIO_MODEL overrides the default claude-opus-5-5)
studio run --story story.txt --run-dir runs/film --duration 90 --agents claude
studio status runs/film
studio approve runs/film story_analyst      # or: studio revise runs/film story_analyst "notes"
                                            #     studio edit runs/film story_analyst fixed.json
# then `studio run ...` again to resume from the gate; human_approval.mode "none" skips the gates
```

Each agent is one structured-output call (`client.messages.parse` with its Pydantic schema); the
Director and the Layout TD make one call per scene and only redo the scenes that fail a check.

Design docs: "Animation Studio: Stage 1 Agent Design" and "Asset Build Implementation Plan".

## Asset build

Stage 1 agents emit asset specs; deterministic Blender code turns them into library files,
so a human modeller only sees what the automated paths cannot do (see the Asset Build
Implementation Plan). Phase 1, parametric characters:

```
python -m tools.make_seed_library --library library      # placeholder bases, garments, hair
python -m tools.build_character --library library --characters runs/film/current/characters.json --rescan
studio build-characters runs/film                         # same, from a run; promotes the sheets to library assets
```

A sheet with `asset.status: parametric` names a base, proportions (sliders 0.7 to 1.3 and
bone-length overrides), materials by slot, a hair piece and an outfit of garments. The
builder opens the base, applies all of it, saves `library/characters/lib_char_<slug>.blend`
and rescans the manifest; the sheet then points at that library id.

Phase 3, composed environments:

```
python -m tools.compose_env --library library --world runs/film/current/world.json --rescan
studio build-environments runs/film
```

A location with `asset.status: composed` names a kit, a seed, bounds, a terrain generator,
the pieces its anchors depend on and scatter layers. The composer builds the set
deterministically from the seed, saves `library/envs/lib_env_<slug>.blend` with anchors and
time-of-day light rigs, and the location then points at that library id.

Phase 2, clips:

```
python -m tools.retarget --library library --source library/clips/source/mixamo/walk_03.fbx \
    --base base_human_child --map tools/bone_maps/mixamo_to_rigify_human.json --name clip_walk_03 --licence CC0 --rescan
```

The retargeter constrains the base rig to the mocap armature through a bone map, bakes one
Action, scales the root travel to the base's height, measures foot sliding (exit code 2 above
`--max-slide-cm`), decides looping, tags the clip from its name and motion
(`tools/tag_clips.py`, vocabulary shared with the Director), and saves it under
`clips/baked/<rig_type>/`. Breathing, blinks, look-at, head follow and feet-on-ground are
procedural layers in `tools/procedural.py`, so those never become clip requests;
`studio.assets.clips.find_clip` resolves a Director `action_tags` set to a clip or a layer.

Phase 4, automated one-off props:

```
studio build-props runs/film --library library [--agents stub] [--only br_prop_lantern_b] [--time-limit 300]
```

For each open prop build request in world.json the Asset Builder agent writes a Blender
script that defines `build(params)`. A static check allows only bpy, bmesh, mathutils, math,
random and colorsys and no file access; the script then runs in a child process with a time
limit and the runner alone saves `library/props/lib_prop_<slug>.blend`. A turntable renders a
contact sheet under `library/preview/`, Render QA scores it against the spec, and the loop
repeats with the notes up to `flags.asset_builder_rounds`. A score at or above
`flags.qa_accept_score` accepts the request (decision by `qa_agent`), points the prop at the
new library id and saves the script with its params and tags to
`library/generators/scripts/index.json` for reuse. Anything else is parked in
`library/preview/pending/` with every attempt recorded for a human. `--agents stub` runs the
loop offline with a box builder and a bounds-only QA.

Phase 5, the queue:

```
studio build-requests runs/film --library library [--save] [--json]
```

Every package already carries its build requests ranked by frames on screen (from
layout.json) with a route chosen in the order `variant` (the nearest library asset fits:
same rig type for characters, bounds within forty percent for props and sets), `generator`
(an accepted script matches the request's tags), `asset_builder` (props), `image_to_3d`
(props with reference images, only when the flag lists it), otherwise `human`. Characters
and clips never go to the automated modelling routes. Near-duplicate requests are routed as
variants of the first one. The command prints the queue with the reason for each route and
writes `assets/build_queue.json`, `assets/metrics.json` and `assets/metrics.md` (requests
opened, closed by code, closed by a human, open, in review, automated share, rounds);
`--save` writes the screen time and routes back into the run's outputs.
