"""Provider registry: name -> adapter class, plus aliases and "provider:model" parsing.

Adding a provider is one class and one ``register_provider`` call::

    from app.ai.providers import JSONTextProvider, register_provider

    class MistralProvider(OpenAIProvider):          # any OpenAI-compatible API
        name = "mistral"
        default_model = "mistral-large-latest"
        api_key_env = ("MISTRAL_API_KEY",)
        default_base_url = "https://api.mistral.ai/v1"

    register_provider(MistralProvider)

after which ``STUDIO_AI_PROVIDER=mistral`` (or a per-task choice) works.
"""

from __future__ import annotations

import os
from typing import Any

from app.ai.providers.base import LLMProvider
from app.ai.providers.types import LLMConfigError

_PROVIDERS: dict[str, type[LLMProvider]] = {}
_ALIASES: dict[str, str] = {}

DEFAULT_PROVIDER_ENV = "STUDIO_LLM_PROVIDER"


def register_provider(cls: type[LLMProvider], aliases: tuple[str, ...] = ()) -> type[LLMProvider]:
    if not cls.name:
        raise ValueError("a provider class needs a name")
    _PROVIDERS[cls.name] = cls
    for a in aliases:
        _ALIASES[a] = cls.name
    return cls


_builtins_loaded = False


def _load_builtins() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    from app.ai.providers.builtin import anthropic, openai, gemini  # noqa: F401, I001  (they register)


def provider_names() -> list[str]:
    _load_builtins()
    return list(_PROVIDERS)


def resolve_name(name: str) -> str:
    _load_builtins()
    key = (name or "").strip().lower()
    key = _ALIASES.get(key, key)
    if key not in _PROVIDERS:
        raise LLMConfigError(f"unknown LLM provider '{name}'; known: {', '.join(provider_names())}")
    return key


def is_provider(name: str) -> bool:
    try:
        resolve_name(name)
        return True
    except LLMConfigError:
        return False


def provider_class(name: str) -> type[LLMProvider]:
    return _PROVIDERS[resolve_name(name)]


def default_provider_name() -> str:
    return resolve_name(os.environ.get(DEFAULT_PROVIDER_ENV) or "anthropic")


def create_provider(name: str | None = None, client: Any = None, **kwargs: Any) -> LLMProvider:
    cls = provider_class(name or default_provider_name())
    return cls(client=client, **kwargs)


def parse_choice(choice: str) -> tuple[str, str | None]:
    """'openai' -> ('openai', None); 'openai:gpt-5' -> ('openai', 'gpt-5').
    Only the first colon splits, so model names with colons ('ollama:llama3:8b') survive."""
    choice = (choice or "").strip()
    head, sep, tail = choice.partition(":")
    return resolve_name(head), (tail.strip() or None) if sep else None


def describe_providers() -> list[dict[str, Any]]:
    """For the admin API: what each provider is, its default model and key status."""
    _load_builtins()
    aliases: dict[str, list[str]] = {}
    for a, n in _ALIASES.items():
        aliases.setdefault(n, []).append(a)
    return [
        {
            "name": n,
            "default_model": cls.resolve_default_model(),
            "model_env": cls.model_env(),
            "api_key_env": list(cls.api_key_env),
            "setup": cls.setup_hint(),
            "configured": cls.configured(),
            "supports_images": cls.supports_images,
            "aliases": sorted(aliases.get(n, [])),
        }
        for n, cls in _PROVIDERS.items()
    ]
