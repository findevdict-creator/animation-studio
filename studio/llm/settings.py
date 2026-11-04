"""Which provider and model each agent uses.

Precedence, highest first: a per-agent choice (``flags.llm_agents`` / ``--llm-agent``), the
run-wide model (``flags.llm_model`` / ``--llm-model``, which applies only to the run-wide
provider), the provider's ``STUDIO_<PROVIDER>_MODEL`` env var, the provider's built-in
default. The run-wide provider is ``flags.llm_provider`` / ``--llm-provider``, else
``STUDIO_LLM_PROVIDER``, else anthropic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from studio.llm.base import LLMProvider
from studio.llm.registry import create_provider, default_provider_name, parse_choice, provider_class, resolve_name
from studio.llm.types import LLMConfigError


@dataclass
class LLMSettings:
    provider: str | None = None
    model: str | None = None
    agents: dict[str, str] = field(default_factory=dict)
    json_repairs: int = 1
    #: pre-built SDK clients (or fakes) per provider name
    clients: dict[str, Any] = field(default_factory=dict)
    _instances: dict[str, LLMProvider] = field(default_factory=dict, repr=False)

    @classmethod
    def from_flags(cls, flags: Any, **overrides: Any) -> "LLMSettings":
        data = {
            "provider": getattr(flags, "llm_provider", None),
            "model": getattr(flags, "llm_model", None),
            "agents": dict(getattr(flags, "llm_agents", {}) or {}),
            "json_repairs": getattr(flags, "llm_json_repairs", 1),
        }
        for k, v in overrides.items():
            if k == "agents" and v:
                data["agents"].update(v)
            elif v is not None:
                data[k] = v
        return cls(**data)

    @property
    def run_provider(self) -> str:
        return resolve_name(self.provider) if self.provider else default_provider_name()

    def choice_for(self, agent: str) -> tuple[str, str]:
        """(provider name, model) for ``agent``."""
        run = self.run_provider
        if agent in self.agents:
            name, model = parse_choice(self.agents[agent])
            if model is None and name == run and self.model:
                model = self.model
        else:
            name, model = run, self.model
        model = model or provider_class(name).resolve_default_model()
        if not model:
            raise LLMConfigError(f"{agent}: provider {name} has no default model; name one ('{name}:MODEL') or set {provider_class(name).model_env()}")
        return name, model

    def provider_for(self, name: str) -> LLMProvider:
        name = resolve_name(name)
        if name not in self._instances:
            self._instances[name] = create_provider(name, client=self.clients.get(name))
        return self._instances[name]

    def for_agent(self, agent: str) -> tuple[LLMProvider, str]:
        name, model = self.choice_for(agent)
        return self.provider_for(name), model

    def summary(self, agents: list[str]) -> dict[str, str]:
        return {a: "{}:{}".format(*self.choice_for(a)) for a in agents}
