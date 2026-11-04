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
| `15-gui` | `studio/gui/` | Local web GUI: start runs, answer approval gates, browse outputs, the package, the build queue and the library |

## Setup

```
pip install -e ".[dev]"
pytest
```

## Running

```
# dry run with canned agents, no API key needed
studio run --story tests/fixtures/lantern_fox/story.txt --run-dir runs/demo --agents stub

# the real agents (Claude by default: needs ANTHROPIC_API_KEY; see "LLM providers" below)
studio run --story story.txt --run-dir runs/film --duration 90 --agents llm
studio status runs/film
studio approve runs/film story_analyst      # or: studio revise runs/film story_analyst "notes"
                                            #     studio edit runs/film story_analyst fixed.json
# then `studio run ...` again to resume from the gate; human_approval.mode "none" skips the gates
```

## LLM providers

Every agent calls its model through a provider adapter (`studio/llm/`). Built in:

| provider | key env var | default model | structured output |
|---|---|---|---|
| `anthropic` (alias `claude`) | `ANTHROPIC_API_KEY` | `claude-opus-5-5` | native schema (`messages.parse`) |
| `openai` | `OPENAI_API_KEY` | `gpt-5` | `response_format` json_schema (non-strict) + schema in prompt |
| `gemini` (alias `google`) | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `gemini-2.5-pro` | JSON mode + schema in prompt (`STUDIO_GEMINI_SCHEMA=native` adds `response_json_schema`) |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-chat` | JSON mode + schema in prompt; no images; output capped at 8192 tokens |
| `openai_compatible` | `STUDIO_OPENAI_COMPATIBLE_BASE_URL` (+ optional `..._API_KEY`) | none, name one | JSON mode + schema in prompt |

Install the SDKs you need: `pip install -e ".[openai]"`, `".[gemini]"` or `".[all-providers]"`.
For providers other than Claude, the reply is validated with the agent's Pydantic model; on a
failure the errors go back to the model for `llm_json_repairs` repair turns (default 1), and
anything still invalid becomes coordinator feedback for the next attempt, like a Claude schema failure.

Choosing, highest precedence first:

```
studio run ... --llm-agent director=gemini --llm-agent layout_td=openai:gpt-5   # per agent
studio run ... --llm-provider openai --llm-model gpt-5                          # run-wide
STUDIO_LLM_PROVIDER=deepseek   STUDIO_OPENAI_MODEL=gpt-5-mini                     # environment
studio providers                                                                 # what is set up
```

The same choices live in the run config as `flags.llm_provider`, `flags.llm_model`,
`flags.llm_agents` (`{"director": "gemini:gemini-2.5-pro"}`) and `flags.llm_json_repairs`, so a
resumed run keeps its models, and the GUI's new-run form has a "Language model" section for
them. `build-props` takes the same `--llm-*` options for the Asset Builder and Render QA.
`STUDIO_MODEL` still sets the Claude default; `STUDIO_<PROVIDER>_MODEL` sets any provider's.

Adding a provider is one class: subclass `OpenAIProvider` for an OpenAI-compatible API, or
`JSONTextProvider` and implement `_generate` for anything else, then `register_provider(cls)`.
The fakes in `tests/fakes.py` (`FakeClient`, `FakeOpenAIClient`, `FakeGeminiClient`) cover
every built-in provider without keys.

## GUI

```
studio gui                                   # http://127.0.0.1:8765/, opens a browser tab
studio gui --agents stub --runs-dir runs     # preselect the offline stub agents
```

A local web page over the same run directories the CLI uses, so a run can be started in one
and finished in the other. It starts a run from a story (paste or load a .txt) with the config
and every flag as form fields, runs the coordinator in a background thread and shows each
agent's state and the log live. At an open gate it shows the output with Approve, Revise
(notes go back to the agent) and Edit (corrected JSON, validated and hard-checked); the run
continues on its own after the decision unless `--no-auto-resume` is given. It also browses
every agent output and version, the production package with its exports, the ranked and
routed build queue (with the same `--save` as `studio build-requests`) and the library
manifest with turntable previews. Flags: `--runs-dir`, `--library`, `--agents`,
`--allow-agents llm,stub`, `--host`, `--port`, `--no-browser`, `--no-auto-resume`. It
binds to 127.0.0.1 and has no login; it uses only the standard library.

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
