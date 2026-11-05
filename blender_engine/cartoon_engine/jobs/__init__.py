"""Job handlers by job type. Each takes a JobContext and returns result data."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cartoon_engine.core.context import JobContext

Handler = Callable[[JobContext], dict[str, Any]]


def handlers() -> dict[str, Handler]:
    from cartoon_engine.jobs import (
        assemble_scene,
        blender_smoke,
        fit_outfit,
        generate_accessory,
        generate_animation,
        generate_character,
        generate_outfit,
        publish_accessory,
        publish_animation,
        publish_character,
        publish_outfit,
        verify_scene,
    )

    return {"assemble_scene": assemble_scene.run, "verify_scene": verify_scene.run,
            "generate_accessory": generate_accessory.run, "publish_accessory": publish_accessory.run,
            "blender_smoke": blender_smoke.run, "generate_character": generate_character.run,
            "publish_character": publish_character.run, "generate_outfit": generate_outfit.run,
            "fit_outfit": fit_outfit.run, "publish_outfit": publish_outfit.run,
            "generate_animation": generate_animation.run, "publish_animation": publish_animation.run}
