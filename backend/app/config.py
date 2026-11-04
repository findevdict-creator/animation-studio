"""Settings from environment variables (prefix ``STUDIO_``) or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STUDIO_", env_file=".env", extra="ignore")

    env: str = Field(default="dev", description="dev | test | prod")
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "postgresql+psycopg://studio:studio@localhost:5432/studio"
    database_echo: bool = False

    redis_url: str = "redis://localhost:6379/0"

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
