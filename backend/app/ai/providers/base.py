"""The provider adapter interface.

``LLMProvider.complete`` makes one structured-output call and returns an ``LLMResult``.
Providers with native schema enforcement (Claude) parse in the SDK; the rest subclass
``JSONTextProvider``, implement ``_generate`` (one raw text call) and inherit the prompted
JSON + Pydantic validation + repair loop from ``app.ai.providers.structured``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import ValidationError

from app.ai.providers.structured import parse_reply, repair_message, schema_instructions
from app.ai.providers.types import LLMConfigError, LLMError, LLMRequest, LLMResult, LLMUnsupported

log = logging.getLogger("app.ai.providers")


class LLMProvider:
    #: registry name, e.g. "openai"
    name: str = ""
    #: model used when neither the config nor STUDIO_<NAME>_MODEL names one
    default_model: str = ""
    #: environment variables that hold the API key, first match wins
    api_key_env: tuple[str, ...] = ()
    #: whether images can go in the user message
    supports_images: bool = True

    def __init__(self, client: Any = None, api_key: str | None = None, base_url: str | None = None):
        self._client = client
        self.api_key = api_key
        self.base_url = base_url

    # -------------------------------------------------------------- config
    @classmethod
    def model_env(cls) -> str:
        return f"STUDIO_{cls.name.upper()}_MODEL"

    @classmethod
    def resolve_default_model(cls) -> str:
        return os.environ.get(cls.model_env()) or cls.default_model

    @classmethod
    def find_api_key(cls) -> str | None:
        for var in cls.api_key_env:
            if os.environ.get(var):
                return os.environ[var]
        return None

    @classmethod
    def configured(cls) -> bool:
        """Whether an API key is present in the environment (a passed-in client also works)."""
        return cls.find_api_key() is not None

    @classmethod
    def setup_hint(cls) -> str:
        """What to set in the environment before this provider works."""
        return " or ".join(cls.api_key_env)

    def require_key(self) -> str:
        key = self.api_key or self.find_api_key()
        if not key:
            raise LLMConfigError(f"{self.name}: set {' or '.join(self.api_key_env)} to use this provider")
        return key

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self.make_client()
        return self._client

    def make_client(self) -> Any:  # pragma: no cover - needs the SDK and a key
        raise NotImplementedError

    # ---------------------------------------------------------------- call
    def check(self, request: LLMRequest) -> None:
        if request.images and not self.supports_images:
            raise LLMUnsupported(f"{self.name} cannot read images; pick another provider for this task")

    def complete(self, request: LLMRequest) -> LLMResult:  # pragma: no cover - interface
        raise NotImplementedError


class JSONTextProvider(LLMProvider):
    """Prompted JSON with validation and repair turns, for providers without strong schema
    enforcement. Subclasses implement ``_generate``."""

    def system_prompt(self, request: LLMRequest) -> str:
        return request.system + schema_instructions(request.output_model)

    def _generate(self, request: LLMRequest, system: str, turns: list[dict[str, str]]) -> tuple[str, str, dict[str, int]]:
        """One call. ``turns`` are earlier assistant/user turns after the first user message
        (repair rounds). Returns (text, stop, usage) with stop in ok|refusal|max_tokens."""
        raise NotImplementedError  # pragma: no cover

    def complete(self, request: LLMRequest) -> LLMResult:
        self.check(request)
        system = self.system_prompt(request)
        turns: list[dict[str, str]] = []
        usage: dict[str, int] = {}
        last_error: Exception | None = None
        calls = 0
        for _ in range(max(0, request.json_repairs) + 1):
            text, stop, u = self._generate(request, system, turns)
            calls += 1
            for k, v in u.items():
                usage[k] = usage.get(k, 0) + v
            if stop != "ok":
                return LLMResult(None, stop=stop, text=text, usage=usage, calls=calls)  # type: ignore[arg-type]
            try:
                parsed = parse_reply(text, request.output_model)
            except (ValidationError, LLMError) as exc:
                last_error = exc
                log.info("%s: reply failed validation (%s); asking for a repair", self.name, type(exc).__name__)
                turns += [{"role": "assistant", "content": text}, {"role": "user", "content": repair_message(exc)}]
                continue
            return LLMResult(parsed, text=text, usage=usage, calls=calls)
        assert last_error is not None
        raise last_error
