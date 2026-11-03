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

## Setup

```
pip install -e ".[dev]"
pytest
```

Design docs: "Animation Studio: Stage 1 Agent Design" and "Asset Build Implementation Plan".
