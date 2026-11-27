"""Application settings, read from environment variables (prefix RAGOH_)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAGOH_", env_file=".env", extra="ignore")

    env: str = "local"  # local | test | staging | production
    database_url: str = "postgresql+psycopg://ragoh:ragoh@localhost:5432/ragoh"
    redis_url: str | None = "redis://localhost:6379/0"

    jwt_secret: str = Field(default="change-me-local-only-not-for-production-use", min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    # Login rate limiting: max failed attempts per username+IP within the window.
    login_max_attempts: int = 5
    login_window_seconds: int = 900

    cors_origins: list[str] = ["*"]
    git_sha: str = "dev"

    # Rough bounding box used to reject visit points with swapped/invalid coordinates.
    country_bbox: tuple[float, float, float, float] = (
        2.6,
        4.2,
        14.7,
        13.9,
    )  # minx, miny, maxx, maxy

    max_import_rows: int = 50_000

    # Where uploaded photos are stored (a Docker volume on the pilot server).
    media_dir: str = "/tmp/ragoh-media"  # noqa: S108
    max_media_bytes: int = 5 * 1024 * 1024
    # Routing engines (docker services on the pilot server). Empty = straight-line fallback.
    osrm_url: str | None = None
    vroom_url: str | None = None
    # Expo push service. Unset access token is fine for the free tier; set "" to disable push.
    expo_push_url: str = "https://exp.host/--/api/v2/push/send"
    expo_access_token: str | None = None
    push_enabled: bool = True
    # Observability: JSON log lines (for CloudWatch/grep) and optional Sentry (self-hosted ok).
    log_json: bool = False
    sentry_dsn: str | None = None
    # Integration secrets (Phase 7b): from SSM Parameter Store, never campaign config.
    es_api_key: str | None = None  # Elasticsearch API key (base64 id:key), or
    es_username: str | None = None
    es_password: str | None = None
    dhis2_password: str | None = None
    digit_password: str | None = None
    digit_client_auth: str = "ZWdvdi11c2VyLWNsaWVudDo="  # DIGIT default "egov-user-client:"
    webhook_secret: str | None = None  # HMAC key for the signed-webhook mode
    routing_timeout_s: float = 10.0
    # Offline map assets (tiles, fonts, style layers) produced by the routing-data workflow.
    tiles_dir: str = "/data/tiles"
    tileset: str = "fct"

    # Max records accepted in one sync push (phones send in batches).
    max_push_records: int = 2000


@lru_cache
def get_settings() -> Settings:
    return Settings()
