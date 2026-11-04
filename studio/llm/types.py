"""Provider-neutral request and result types for one structured-output LLM call."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

StopReason = Literal["ok", "refusal", "max_tokens", "error"]


class LLMError(Exception):
    """A provider could not produce a usable answer (bad JSON after repairs, empty reply)."""


class LLMUnsupported(LLMError):
    """The provider or model cannot do what the request needs (for example, read images)."""


class LLMConfigError(LLMError):
    """Unknown provider, missing SDK or missing API key."""


@dataclass(frozen=True)
class ImagePart:
    """An image sent ahead of the text in the user message."""

    data: bytes
    media_type: str = "image/png"

    @property
    def b64(self) -> str:
        return base64.standard_b64encode(self.data).decode("ascii")


@dataclass
class LLMRequest:
    model: str
    system: str
    user: str
    output_model: type[BaseModel]
    max_tokens: int = 16000
    effort: str | None = None
    images: list[ImagePart] = field(default_factory=list)
    json_repairs: int = 1


@dataclass
class LLMResult:
    parsed: BaseModel | None
    stop: StopReason = "ok"
    detail: str = ""
    text: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    calls: int = 1
