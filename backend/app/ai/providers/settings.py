"""Which provider and model each AI task uses.

Precedence, highest first: a per-task choice (``STUDIO_AI_TASKS='{"interpret_character": "openai:gpt-5"}'``),
the deployment-wide model (``STUDIO_AI_MODEL``, which applies only to the deployment-wide
provider), the provider's ``STUDIO_<PROVIDER>_MODEL`` env var, the provider's built-in
default. The deployment-wide provider is ``STUDIO_AI_PROVIDER``, else ``STUDIO_LLM_PROVIDER``,
else anthropic. A job may name a provider in its params; that wins over all of these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ai.providers.base import LLMProvider
from app.ai.providers.registry import create_provider, default_provider_name, parse_choice, provider_class, resolve_name
from app.ai.providers.types import LLMConfigError


@dataclass
class LLMSettings:
    provider: str | None = None
    model: str | None = None
    tasks: dict[str, str] = field(default_factory=dict)
    json_repairs: int = 1
    #: pre-built SDK clients (or fakes) per provider name
    clients: dict[str, Any] = field(default_factory=dict)
    _instances: dict[str, LLMProvider] = field(default_factory=dict, repr=False)

    @classmethod
    def from_app_settings(cls, settings: Any, **overrides: Any) -> "LLMSettings":
        data: dict[str, Any] = {
            "provider": settings.ai_provider, "model": settings.ai_model, "tasks": dict(settings.ai_tasks),
            "json_repairs": settings.ai_json_repairs,
        }
        data.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**data)

    @property
    def default_provider(self) -> str:
        return resolve_name(self.provider) if self.provider else default_provider_name()

    def choice_for(self, task: str, override: str | None = None) -> tuple[str, str]:
        """(provider name, model) for ``task``; ``override`` is a job's own ``provider[:model]``."""
        default = self.default_provider
        choice = override or self.tasks.get(task)
        if choice:
            name, model = parse_choice(choice)
            if model is None and name == default and self.model:
                model = self.model
        else:
            name, model = default, self.model
        model = model or provider_class(name).resolve_default_model()
        if not model:
            raise LLMConfigError(f"{task}: provider {name} has no default model; name one ('{name}:MODEL') "
                                 f"or set {provider_class(name).model_env()}")
        return name, model

    def provider_for(self, name: str) -> LLMProvider:
        name = resolve_name(name)
        if name not in self._instances:
            self._instances[name] = create_provider(name, client=self.clients.get(name))
        return self._instances[name]

    def for_task(self, task: str, override: str | None = None) -> tuple[LLMProvider, str]:
        name, model = self.choice_for(task, override)
        return self.provider_for(name), model
