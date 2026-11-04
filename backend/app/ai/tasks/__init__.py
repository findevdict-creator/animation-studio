"""AI tasks run by the ``ai`` worker. Each takes a claimed job and returns its result data."""

from __future__ import annotations

from app.ai.tasks.interpret_character import run as interpret_character
from app.ai.tasks.interpret_outfit import run as interpret_outfit

AI_TASKS = {"interpret_character": interpret_character, "interpret_outfit": interpret_outfit}
