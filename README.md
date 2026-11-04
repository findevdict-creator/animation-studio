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
