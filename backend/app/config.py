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

    # Object storage (any S3 API). Each process gets its own identity; see infra/seaweedfs.
    s3_endpoint_url: str | None = "http://localhost:8333"
    s3_public_endpoint_url: str | None = Field(
        default=None, description="host the browser uses for presigned URLs, when it differs from s3_endpoint_url"
    )
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_library_bucket: str = "studio-library"
    s3_work_bucket: str = "studio-work"
    s3_conditional_writes: bool = Field(
        default=True, description="the store honours If-None-Match: * on PutObject and CompleteMultipartUpload"
    )
    s3_presign_expiry_s: int = Field(default=900, ge=60, le=3600)

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
