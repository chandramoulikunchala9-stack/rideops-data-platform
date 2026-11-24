"""Lens settings (see docs/ReContruction/lens-system-design.md §23).

Lens is the multi-tenant "connect any database" module. It is off by default
(`LENS_ENABLED=false`) so the existing RideOps dashboard keeps working unchanged.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from rideops.config import PostgresSettings


class LensSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    lens_enabled: bool = False
    # Require sign-in for the existing RideOps routes. Off until the dashboard has a login
    # screen (Phase 12), so enabling Lens does not lock the current dashboard out.
    lens_protect_rideops: bool = False
    # Process role (ADR-6): `all` runs API + one worker + scheduler in one process (free tier).
    lens_role: Literal["all", "api", "worker", "scheduler"] = "all"
    # Metadata database. Empty means "use the RideOps POSTGRES_* database" (schema `app`).
    database_url: SecretStr | None = None

    public_app_url: str = "http://localhost:5173"
    session_cookie_secure: bool = True  # set false only for plain-http local development
    session_idle_hours: int = 12
    session_absolute_days: int = 7
    email_backend: Literal["console", "memory"] = "console"
    internal_token: SecretStr | None = None  # protects /internal/tick; unset = endpoint disabled

    login_max_failures: int = 10  # per email within login_failure_window_min
    login_failure_window_min: int = 15
    login_max_per_ip_per_min: int = 10

    # Connections (Phase 12)
    lens_master_keys: SecretStr | None = None  # "1:<base64 32 bytes>,2:<...>"; see docs (KEK ring)
    lens_active_key_version: int | None = None  # default: highest version in the ring
    allow_private_networks: bool = False  # true only when self-hosted next to private databases
    allowed_ports: str = "443,1433,1521,3306,5432,6432,27017"
    egress_ips: str = ""  # shown in the UI so users can allowlist Lens
    health_timeout_s: int = 10
    max_target_sessions: int = 2
    rideops_demo_url: SecretStr | None = None  # read-only URL to the RideOps DB → demo connection
    connection_test_max_age_min: int = 10

    worker_poll_interval_s: float = 1.0
    scheduler_interval_s: float = 15.0

    def metadata_conninfo(self) -> str:
        if self.database_url is not None:
            # Accept SQLAlchemy-style URLs (postgresql+psycopg://) as documented in §23.
            return self.database_url.get_secret_value().replace(
                "postgresql+psycopg://", "postgresql://", 1
            )
        return PostgresSettings().conninfo()


@lru_cache
def get_lens_settings() -> LensSettings:
    return LensSettings()


def allowed_port_set(settings: LensSettings) -> set[int]:
    return {int(p) for p in settings.allowed_ports.split(",") if p.strip().isdigit()}
