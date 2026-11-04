"""LLM provider adapters behind the agents.

Every agent makes structured-output calls through an ``LLMProvider``: Claude (Anthropic),
OpenAI, Gemini, DeepSeek and any OpenAI-compatible endpoint are built in, and more can be
added with ``register_provider``. Which provider and model an agent uses comes from the
run config (``flags.llm_provider``, ``flags.llm_model``, ``flags.llm_agents``), the CLI
(``--llm-provider``, ``--llm-model``, ``--llm-agent``) or the environment
(``STUDIO_LLM_PROVIDER``, ``STUDIO_<PROVIDER>_MODEL``).
"""

from studio.llm.base import JSONTextProvider, LLMProvider
from studio.llm.registry import (
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
from studio.llm.settings import LLMSettings
from studio.llm.types import ImagePart, LLMConfigError, LLMError, LLMRequest, LLMResult, LLMUnsupported

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
