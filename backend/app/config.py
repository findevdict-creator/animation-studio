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
    job_visibility_timeout_s: int = Field(default=6 * 3600, description="above the longest job timeout")

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

    # Workers (architecture 11). The Blender process gets none of the credentials above.
    work_root: str = "/tmp/studio-work"
    blender_mode: str = Field(default="binary", description="binary: the Blender executable; wheel: the bpy module in this Python")
    blender_binary: str = "blender"
    engine_entrypoint: str | None = Field(default=None, description="path to cartoon_engine/entrypoint.py; found automatically")
    job_timeouts_s: dict[str, int] = Field(default_factory=lambda: {"blender_smoke": 300, "generate_character": 1800,
                                                                              "publish_character": 900, "generate_outfit": 1800,
                                                                              "fit_outfit": 900, "publish_outfit": 900,
                                                                              "generate_accessory": 900,
                                                                              "publish_accessory": 900,
                                                                              "assemble_scene": 1800,
                                                                              "verify_scene": 1200})
    default_job_timeout_s: int = 1800
    keep_failed_workspaces: bool = True
    worker_host: str | None = None

    # Authentication (architecture 14): OIDC bearer tokens, Keycloak in the dev stack.
    auth_issuer: str = "http://localhost:8080/realms/studio"
    auth_audience: str = "studio-api"
    auth_jwks_url: str | None = Field(default=None, description="default: <issuer>/protocol/openid-connect/certs")
    auth_jwks_json: str | None = Field(default=None, description="a static JWKS document instead of fetching one")
    auth_algorithms: list[str] = Field(default_factory=lambda: ["RS256", "ES256"])
    auth_leeway_s: int = 30
    auth_roles_claim: str = Field(default="realm_access.roles", description="dotted path to the role list in the token")

    # AI (architecture D12). Provider API keys stay in the provider's own env var (ANTHROPIC_API_KEY, …)
    # and only the ai worker gets them.
    ai_provider: str | None = Field(default=None, description="anthropic | openai | gemini | deepseek | openai_compatible")
    ai_model: str | None = None
    ai_tasks: dict[str, str] = Field(default_factory=dict, description='per task "provider[:model]"')
    ai_json_repairs: int = Field(default=1, ge=0, le=3)
    ai_semantic_repairs: int = Field(default=1, ge=0, le=3, description="repair turns for style-profile issues")

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
