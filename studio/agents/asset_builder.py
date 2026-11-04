"""Asset Builder: writes a bpy script that builds one prop from a build request (asset build
phase 4). The script runs in the sandbox in tools/asset_builder/sandbox.py; the Render QA
agent judges the turntable; the loop in studio/assets/builder.py gives this agent the QA
notes and the previous script for the next round."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from studio.agents.base import ClaudeAgent
from studio.schemas import Config, Manifest
from studio.schemas.build_requests import BuildAttempt, BuildRequest
from studio.schemas.common import StudioModel

SYSTEM_PROMPT = """You are the Asset Builder of an animation studio. You write Blender Python that models one
prop from a written specification. Your script runs headless in a sandbox and must follow
these rules exactly:

- Define a top-level function `build(params)` where `params` is a dict of floats. Put every
  dimension and colour you might want to tune into `params`, with the defaults you return.
- Import only bpy, bmesh, mathutils, math, random, colorsys. No other modules, no file
  access, no `open`, `exec`, `eval`, `getattr`, no `bpy.ops.wm`, no saving, loading or
  rendering. The runner saves the file; the scene starts empty.
- Build in metres, Z up, the prop resting on the ground (lowest point at z = 0), centred on
  the origin in X and Y, matching the requested bounds within ten percent.
- Use mesh primitives, bmesh, modifiers (bevel, subdivision, solidify, array, mirror) and
  simple Principled BSDF materials set with `use_nodes = True`. Name objects and materials
  after what they are. Apply object scale yourself or leave it at 1.
- Keep the prop between 200 and 20,000 polygons. Stylised, clean silhouettes read better than
  detail at this scale.
- Deterministic: seed `random` from `params.get("seed", 0)` if you use it.

If a previous attempt exists, fix what the QA notes say and keep what worked. Return the
whole script each time, not a diff. Return only the structured output."""


class BuildScript(StudioModel):
    script: str = Field(min_length=20)
    params: dict[str, float] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=8)
    summary: str = ""


class AssetBuilder(ClaudeAgent):
    name = "asset_builder"
    output_model = BuildScript
    system_prompt = SYSTEM_PROMPT
    max_tokens = 12000

    def request_message(self, request: BuildRequest, manifest: Manifest, config: Config, previous: list[BuildAttempt], scripts: dict[str, str]) -> str:
        parts = [f"## Visual style\n{config.visual_style}", f"## Build request\n```json\n{json.dumps(request.model_dump(mode='json', include={'id', 'kind', 'for_id', 'spec', 'bounds_m', 'nearest_library_id', 'reference_images'}), indent=2)}\n```"]
        if request.nearest_library_id:
            near = next((p for p in manifest.props if p.id == request.nearest_library_id), None)
            if near is not None:
                parts.append(f"## Nearest library prop\n```json\n{near.model_dump_json(indent=2)}\n```\nThe request wants something different from this; the entry shows the scale and tags the library uses.")
        if previous:
            notes = []
            for a in previous:
                notes.append(f"### Round {a.round} ({a.route}), QA score {a.qa_score if a.qa_score is not None else 'none'}\n{a.qa_notes or 'no notes'}")
                if a.script and a.script in scripts:
                    notes.append(f"```python\n{scripts[a.script]}\n```")
            parts.append("## Previous attempts\n" + "\n\n".join(notes))
        parts.append("## Task\nWrite the script. Return the complete script, the params it reads with their default values, up to eight library tags for the prop, and a one-line summary.")
        return "\n\n".join(parts)

    def write_script(self, request: BuildRequest, manifest: Manifest, config: Config, previous: list[BuildAttempt] | None = None, scripts: dict[str, str] | None = None) -> BuildScript:
        return self.call(self.request_message(request, manifest, config, list(previous or []), scripts or {}), BuildScript)


def default_params(request: BuildRequest) -> dict[str, Any]:
    bounds = request.bounds_m or (0.3, 0.3, 0.3)
    return {"width": float(bounds[0]), "depth": float(bounds[1]), "height": float(bounds[2]), "seed": 0.0}
