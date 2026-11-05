"""Job handlers by job type. Each takes a JobContext and returns result data."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cartoon_engine.core.context import JobContext

Handler = Callable[[JobContext], dict[str, Any]]


def handlers() -> dict[str, Handler]:
    from cartoon_engine.jobs import (
        blender_smoke,
        fit_outfit,
        generate_accessory,
        generate_character,
        generate_outfit,
        publish_accessory,
        publish_character,
        publish_outfit,
    )

    return {"generate_accessory": generate_accessory.run, "publish_accessory": publish_accessory.run,
            "blender_smoke": blender_smoke.run, "generate_character": generate_character.run,
            "publish_character": publish_character.run, "generate_outfit": generate_outfit.run,
            "fit_outfit": fit_outfit.run, "publish_outfit": publish_outfit.run}
