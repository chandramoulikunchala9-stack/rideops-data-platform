"""Application settings, read from environment variables (and an optional `.env` file).

Every setting has a safe local default, so the project runs without any configuration.
To override one, set an environment variable with the `RIDEOPS_` prefix, e.g. `RIDEOPS_SEED=7`.
"""

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings. Pydantic converts and validates each value (e.g. "7" -> 7)."""

    model_config = SettingsConfigDict(
        env_prefix="RIDEOPS_",  # field `seed` is read from env var RIDEOPS_SEED
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env also holds POSTGRES_*, AWS_* etc. for other components
    )

    env: Literal["local", "dev", "prod"] = "local"
    log_level: str = "INFO"
    data_dir: Path = Path("data")
    seed: int = 42
    scale: Literal["small", "full", "large"] = "small"
    # Generated data ends on this date. A fixed date (instead of "today") keeps output reproducible.
    data_end_date: date = date(2026, 10, 31)


@lru_cache
def get_settings() -> Settings:
    """Return one shared Settings instance (created on first call)."""
    return Settings()
