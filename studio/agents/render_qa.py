"""Render QA: looks at a turntable contact sheet of an automatically built prop and scores it
against the build request (asset build phase 4). The score decides acceptance through
``flags.qa_accept_score``; the notes feed the next Asset Builder round."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from pydantic import Field

from studio.agents.base import ClaudeAgent
from studio.schemas import Config
from studio.schemas.build_requests import BuildRequest
from studio.schemas.common import StudioModel

SYSTEM_PROMPT = """You are Render QA for an animation studio. You see a contact sheet: turntable frames of one
prop that code built from a written specification, on a transparent background, left to right
and top to bottom around the object. Judge whether a viewer would recognise the prop as the
specification describes it and whether it fits the production's visual style.

Score from 0 to 1: 1.0 is production ready; 0.8 reads clearly as the specified object with
only cosmetic faults; 0.5 is recognisable but wrong in a way a modeller must fix; below 0.3 it
is not the object. The measured bounds and polygon count are given; weigh a bounds mismatch
above ten percent as a real fault. List concrete, fixable problems in the order a modeller
should address them, each one naming the part and the fix. Return only the structured output."""


class QAVerdict(StudioModel):
    score: float = Field(ge=0, le=1)
    matches: list[str] = Field(default_factory=list, max_length=8)
    problems: list[str] = Field(default_factory=list, max_length=8)
    notes: str = ""

    def summary(self) -> str:
        return "; ".join(self.problems) if self.problems else (self.notes or "no problems noted")


class RenderQA(ClaudeAgent):
    name = "render_qa"
    output_model = QAVerdict
    system_prompt = SYSTEM_PROMPT
    max_tokens = 2000

    def review(self, contact_sheet: Path, request: BuildRequest, metrics: dict[str, Any], config: Config) -> QAVerdict:
        image = base64.standard_b64encode(Path(contact_sheet).read_bytes()).decode("ascii")
        text = (
            f"## Visual style\n{config.visual_style}\n\n## Build request\n```json\n"
            f"{json.dumps(request.model_dump(mode='json', include={'id', 'kind', 'for_id', 'spec', 'bounds_m'}), indent=2)}\n```\n\n"
            f"## Measured\n```json\n{json.dumps(metrics)}\n```\n\n## Task\nScore the prop and list its problems."
        )
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": self.effort},
            output_format=QAVerdict,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image}},
                        {"type": "text", "text": text},
                    ],
                }
            ],
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            return QAVerdict(score=0.0, problems=["QA gave no answer"], notes="refused or empty")
        return response.parsed_output
