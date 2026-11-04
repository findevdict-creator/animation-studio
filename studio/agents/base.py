"""Base class for LLM agents.

One call per agent run: a system prompt (role, rules, ID formats), a user message with
the story, the config, upstream JSON and any feedback, and the agent's output schema. The
call goes through a provider adapter (``studio.llm``): Claude enforces the schema natively
(``client.messages.parse(output_format=Model)``, cached system prompt); OpenAI, Gemini,
DeepSeek and OpenAI-compatible endpoints get prompted JSON validated with Pydantic and
repaired. The coordinator validates the result again and runs the hard checks; the agent
itself never touches files.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from studio.coordinator.agents import AgentContext
from studio.ids import shot_scene
from studio.llm import ImagePart, LLMProvider, LLMRequest, LLMResult, create_provider
from studio.llm.providers.anthropic import AnthropicProvider
from studio.schemas import StudioModel
from studio.schemas.continuity import Issue

log = logging.getLogger("studio.agents")

DEFAULT_MODEL = AnthropicProvider.resolve_default_model()
DEFAULT_EFFORT = os.environ.get("STUDIO_EFFORT", "high")
#: flags that choose the LLM; kept out of the prompt because they mean nothing to the model
LLM_FLAG_KEYS = {"llm_provider", "llm_model", "llm_agents", "llm_json_repairs"}


class AgentRefused(Exception):
    """The model declined the request; the coordinator treats this as a failed attempt."""


class LLMAgent:
    name: str = ""
    output_model: type[StudioModel]
    system_prompt: str = ""
    max_tokens: int = 16000

    def __init__(
        self,
        client: Any = None,
        model: str | None = None,
        effort: str = DEFAULT_EFFORT,
        provider: LLMProvider | str | None = None,
        json_repairs: int = 1,
    ):
        """``provider`` is an adapter or a provider name (default: STUDIO_LLM_PROVIDER, else
        anthropic). ``client`` is a pre-built SDK client (or a fake) for that provider."""
        if isinstance(provider, LLMProvider):
            self.provider = provider
        else:
            self.provider = create_provider(provider, client=client)
        self.model = model or self.provider.resolve_default_model()
        self.effort = effort
        self.json_repairs = json_repairs
        self.last_usage: dict[str, int] = {}

    @property
    def client(self) -> Any:
        return self.provider.client

    @property
    def llm_label(self) -> str:
        return f"{self.provider.name}:{self.model}"

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
                    "flags": cfg.flags.model_dump(mode="json", exclude=LLM_FLAG_KEYS),
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
    def complete(
        self,
        user_message: str,
        output_model: type[StudioModel],
        system_prompt: str | None = None,
        images: list[ImagePart] | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        """One structured-output call through the provider; returns the raw result (refusals
        and truncation are reported in ``result.stop``, not raised)."""
        log.info("%s: calling %s for %s (%d chars)", self.name, self.llm_label, output_model.__name__, len(user_message))
        result = self.provider.complete(
            LLMRequest(
                model=self.model,
                system=system_prompt or self.system_prompt,
                user=user_message,
                output_model=output_model,
                max_tokens=max_tokens or self.max_tokens,
                effort=effort or self.effort,
                images=list(images or []),
                json_repairs=self.json_repairs,
            )
        )
        for key, value in result.usage.items():
            self.last_usage[key] = self.last_usage.get(key, 0) + value
        return result

    def call(
        self,
        user_message: str,
        output_model: type[StudioModel],
        system_prompt: str | None = None,
        images: list[ImagePart] | None = None,
        effort: str | None = None,
    ) -> StudioModel:
        """One structured-output call. Agents that split their work (the Director works
        per scene) call this several times from ``run``."""
        result = self.complete(user_message, output_model, system_prompt, images, effort)
        if result.stop == "refusal":
            raise AgentRefused(f"{self.name}: request declined ({result.detail or None})")
        if result.stop == "max_tokens":
            raise RuntimeError(f"{self.name}: output truncated at {self.max_tokens} tokens; raise max_tokens")
        if result.parsed is None:
            raise RuntimeError(f"{self.name}: no parsed output in the response")
        parsed = result.parsed
        if isinstance(parsed, output_model):
            return parsed
        return output_model.model_validate(parsed.model_dump() if hasattr(parsed, "model_dump") else parsed)

    def run(self, ctx: AgentContext) -> StudioModel:
        self.last_usage = {}
        return self.call(self.build_user_message(ctx), self.output_model)


#: The name the agents were written against; every agent subclasses this.
ClaudeAgent = LLMAgent


def scene_of_issue(issue: Issue, world) -> str | None:
    """Which scene an issue belongs to, from its ``where`` (shot, scene or beat id)."""
    where = issue.where or ""
    if where.startswith("sh_"):
        return shot_scene(where)
    if where.startswith("sc_"):
        return where
    if where.startswith("beat_"):
        return next((s.id for s in world.scenes if where in s.beat_ids), None)
    return None


def scenes_to_redo(feedback: list[Issue], scene_ids: list[str], world) -> tuple[set[str], dict[str, list[Issue]]]:
    """Per-scene agents redo only the scenes that own an issue. An issue that cannot be
    placed is global: every scene hears it and is redone. No feedback means do everything."""
    per_scene: dict[str, list[Issue]] = {sid: [] for sid in scene_ids}
    if not feedback:
        return set(scene_ids), per_scene
    redo: set[str] = set()
    for issue in feedback:
        sid = scene_of_issue(issue, world)
        if sid in per_scene:
            per_scene[sid].append(issue)
            redo.add(sid)
        else:
            redo = set(scene_ids)
            for other in scene_ids:
                per_scene[other].append(issue)
    return redo, per_scene
