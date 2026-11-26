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
    # Max records accepted in one sync push (phones send in batches).
    max_push_records: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()
