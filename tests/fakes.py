"""A fake Claude client for agent tests. ``client.messages.parse`` records every request
and returns canned responses, so prompt assembly and parsing are tested offline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeUsage:
    input_tokens: int = 1000
    output_tokens: int = 500
    cache_read_input_tokens: int = 0


@dataclass
class FakeResponse:
    parsed_output: Any
    stop_reason: str = "end_turn"
    stop_details: Any = None
    usage: FakeUsage = field(default_factory=FakeUsage)


class FakeMessages:
    """Returns responses in order; the last one repeats. Keys responses by the agent's
    output_format when given a dict, so one client can serve several agents."""

    def __init__(self, responses):
        self.by_model = responses if isinstance(responses, dict) else None
        self.responses = [] if self.by_model is not None else list(responses)
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.by_model is not None:
            return self.by_model[kwargs["output_format"]]
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def for_agent(self, output_format) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["output_format"] is output_format]


class FakeClient:
    def __init__(self, *responses, **by_schema):
        if by_schema:
            self.messages = FakeMessages(by_schema["by_schema"])
        else:
            self.messages = FakeMessages(responses)
