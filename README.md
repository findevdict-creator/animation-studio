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

## Setup

```
pip install -e ".[dev]"
pytest
```

Design docs: "Animation Studio: Stage 1 Agent Design" and "Asset Build Implementation Plan".
