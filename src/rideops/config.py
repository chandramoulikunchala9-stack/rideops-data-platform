"""Application settings, read from environment variables (and an optional `.env` file).

Every setting has a safe local default, so the project runs without any configuration.
To override one, set an environment variable with the `RIDEOPS_` prefix, e.g. `RIDEOPS_SEED=7`.
"""

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root (src/rideops/config.py -> parents[2]); used to locate sql/ and data/.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = PROJECT_ROOT / "sql"


class PostgresSettings(BaseSettings):
    """Database connection, read from POSTGRES_* variables. The password has no default."""

    model_config = SettingsConfigDict(
        env_prefix="POSTGRES_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "localhost"
    port: int = 5432
    db: str = "rideops"
    user: str = "rideops"
    password: SecretStr  # SecretStr hides the value in logs and repr()
    sslmode: str = "prefer"  # hosted databases (e.g. Neon) need "require"

    def conninfo(self, dbname: str | None = None) -> str:
        return (
            f"host={self.host} port={self.port} dbname={dbname or self.db} "
            f"user={self.user} password={self.password.get_secret_value()} "
            f"sslmode={self.sslmode} application_name=rideops connect_timeout=5"
        )


class ApiSettings(BaseSettings):
    """Backend API settings (variables without prefix, see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_cors_origins: str = "http://localhost:5173"  # comma-separated
    retry_runner_enabled: bool = False
    monthly_budget_usd: float = 1200.0
    alert_webhook_url: str | None = None  # optional Slack/Teams-compatible incoming webhook

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


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
