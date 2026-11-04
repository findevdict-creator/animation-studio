"""Job handlers by job type. Each takes a JobContext and returns result data."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cartoon_engine.core.context import JobContext

Handler = Callable[[JobContext], dict[str, Any]]


def handlers() -> dict[str, Handler]:
    from cartoon_engine.jobs import blender_smoke, generate_character, publish_character

    return {"blender_smoke": blender_smoke.run, "generate_character": generate_character.run,
            "publish_character": publish_character.run}
