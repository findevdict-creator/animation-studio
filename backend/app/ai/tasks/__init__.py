"""AI tasks run by the ``ai`` worker. Each takes a claimed job and returns its result data."""

from __future__ import annotations

from app.ai.tasks.interpret_character import run as interpret_character

AI_TASKS = {"interpret_character": interpret_character}
