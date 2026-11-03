"""Base class for agents on the Claude API.

One call per agent run: a cached system prompt (role, rules, ID formats), a user
message with the story, the config, upstream JSON and any feedback, and the
agent's output schema enforced through structured outputs
(``client.messages.parse(output_format=Model)``). The coordinator validates the
result again and runs the hard checks; the agent itself never touches files.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic

from studio.coordinator.agents import AgentContext
from studio.schemas import StudioModel

log = logging.getLogger("studio.agents")

DEFAULT_MODEL = os.environ.get("STUDIO_MODEL", "claude-opus-5-5")
DEFAULT_EFFORT = os.environ.get("STUDIO_EFFORT", "high")


class AgentRefused(Exception):
    """Claude declined the request; the coordinator treats this as a failed attempt."""


class ClaudeAgent:
    name: str = ""
    output_model: type[StudioModel]
    system_prompt: str = ""
    max_tokens: int = 16000

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
    ):
        self._client = client
        self.model = model
        self.effort = effort
        self.last_usage: dict[str, int] = {}

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    # ------------------------------------------------------- prompt parts
    def upstream_sections(self, ctx: AgentContext) -> list[tuple[str, Any]]:
        """(title, json-able) pairs this agent reads. Subclasses override."""
        return []

    def task_prompt(self, ctx: AgentContext) -> str:
        """The instruction at the end of the user message. Subclasses override."""
        return f"Produce the {self.name} output as JSON matching the schema."

    def build_user_message(self, ctx: AgentContext) -> str:
        parts: list[str] = []
        cfg = ctx.config
        parts.append(
            "## Production config\n"
            + json.dumps(
                {
                    "target_duration_sec": cfg.target_duration_sec,
                    "fps": cfg.fps,
                    "visual_style": cfg.visual_style,
                    "aspect_ratio": cfg.aspect_ratio,
                    "audience": cfg.audience,
                    "language": cfg.language,
                    "flags": cfg.flags.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        parts.append("## Story\n" + ctx.story_text.strip())
        for title, payload in self.upstream_sections(ctx):
            text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
            parts.append(f"## {title}\n{text}")
        if ctx.feedback:
            lines = "\n".join(f"- [{i.category}] {i.message}" + (f" Fix: {i.suggested_fix}" if i.suggested_fix else "") for i in ctx.feedback)
            parts.append(
                f"## Problems with your previous attempt (attempt {ctx.attempt - 1})\n"
                "Your last output failed these checks. Fix every one of them and return the complete output again:\n"
                + lines
            )
        if ctx.revise_notes:
            parts.append("## Revision notes from the human reviewer\n" + ctx.revise_notes)
        parts.append("## Task\n" + self.task_prompt(ctx))
        return "\n\n".join(parts)

    # ------------------------------------------------------------- the call
    def run(self, ctx: AgentContext) -> StudioModel:
        user_message = self.build_user_message(ctx)
        log.info("%s: calling %s (%d chars)", self.name, self.model, len(user_message))
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": self.effort},
            output_format=self.output_model,
            messages=[{"role": "user", "content": user_message}],
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.last_usage = {
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            }
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise AgentRefused(f"{self.name}: request declined ({getattr(details, 'category', None)})")
        if response.stop_reason == "max_tokens":
            raise RuntimeError(f"{self.name}: output truncated at {self.max_tokens} tokens; raise max_tokens")
        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError(f"{self.name}: no parsed output in the response")
        return parsed
