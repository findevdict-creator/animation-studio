"""LLM provider adapters (ported from ``studio/llm``, PR #16).

Every AI task makes structured-output calls through an ``LLMProvider``: Claude (Anthropic),
OpenAI, Gemini, DeepSeek and any OpenAI-compatible endpoint are built in, and more can be
added with ``register_provider``. Which provider and model a task uses comes from
``LLMSettings`` (see ``settings.py``). Provider SDKs are imported lazily, so a deployment
installs only the ones it uses.
"""

from app.ai.providers.base import JSONTextProvider, LLMProvider
from app.ai.providers.registry import (
    create_provider,
    default_provider_name,
    describe_providers,
    is_provider,
    parse_choice,
    provider_class,
    provider_names,
    register_provider,
    resolve_name,
)
from app.ai.providers.settings import LLMSettings
from app.ai.providers.types import ImagePart, LLMConfigError, LLMError, LLMRequest, LLMResult, LLMUnsupported

__all__ = [
    "ImagePart",
    "JSONTextProvider",
    "LLMConfigError",
    "LLMError",
    "LLMProvider",
    "LLMRequest",
    "LLMResult",
    "LLMSettings",
    "LLMUnsupported",
    "create_provider",
    "default_provider_name",
    "describe_providers",
    "is_provider",
    "parse_choice",
    "provider_class",
    "provider_names",
    "register_provider",
    "resolve_name",
]
