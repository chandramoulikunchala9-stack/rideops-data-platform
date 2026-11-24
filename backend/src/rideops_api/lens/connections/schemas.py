"""Request/response models for connections and status (§13.3, §13.4). No model ever carries a secret
out of the API: responses only say `has_password`."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

TlsMode = Literal["disable", "require", "verify-ca", "verify-full"]
Environment = Literal["prod", "staging", "dev"]


class SecretIn(BaseModel):
    password: str | None = Field(default=None, max_length=1024)
    ca_cert_pem: str | None = Field(default=None, max_length=20_000)
    client_cert_pem: str | None = Field(default=None, max_length=20_000)
    client_key_pem: str | None = Field(default=None, max_length=20_000)


class DraftIn(BaseModel):
    """A connection config to test or save: a URL, form fields, or both (fields override the URL)."""

    url: str | None = Field(default=None, max_length=4096)
    engine: str | None = None
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    options: dict[str, str] = Field(default_factory=dict)
    tls_mode: TlsMode | None = None
    secret: SecretIn = Field(default_factory=SecretIn)


class ParseUrlIn(BaseModel):
    url: str = Field(max_length=4096)


class ParsedOut(BaseModel):
    engine: str
    host: str
    port: int
    database: str | None
    username: str | None
    options: dict[str, str]
    tls_mode: str
    has_password: bool
    display_url: str


class StageOut(BaseModel):
    stage: str
    ok: bool
    ms: int
    message: str = ""
    code: str | None = None
    warning: bool = False


class TestOut(BaseModel):
    test_id: str
    ok: bool
    server_version: str | None
    stages: list[StageOut]
    capabilities: list[str]
    privilege_findings: list[dict[str, Any]]
    grant_script: str | None = None


class ConnectionCreate(BaseModel):
    test_id: UUID
    draft: DraftIn
    name: str = Field(min_length=1, max_length=80)
    environment: Environment = "dev"
    tags: list[str] = Field(default_factory=list, max_length=20)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    check_interval_s: Literal[60, 300, 900, 3600] = 300
    force_reason: str | None = Field(default=None, max_length=500)  # admins: save a failing test


class ConnectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    environment: Environment | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    check_interval_s: Literal[60, 300, 900, 3600] | None = None


class RotateIn(BaseModel):
    test_id: UUID
    draft: DraftIn


class ConnectionOut(BaseModel):
    id: UUID
    name: str
    engine: str
    environment: str
    host: str | None
    port: int | None
    database: str | None
    username: str | None
    display_url: str
    tls_mode: str
    tags: list[str]
    color: str | None
    check_interval_s: int
    is_paused: bool
    is_demo: bool
    current_state: str
    health_score: int | None
    server_version: str | None
    capabilities: list[str]
    last_checked_at: datetime | None
    last_state_change_at: datetime | None
    created_at: datetime
    deleted_at: datetime | None = None
    has_secret: bool = True
    sparkline: list[int | None] = Field(default_factory=list)  # last latencies, oldest first


class SnapshotOut(BaseModel):
    taken_at: datetime
    state: str
    reachable: bool
    latency_ms: int | None
    error_code: str | None
    error_message: str | None
    server_version: str | None
    uptime_s: int | None
    db_size_bytes: int | None
    health_score: int | None
    score_breakdown: list[dict[str, Any]]
    metrics: dict[str, Any]
    details: dict[str, Any]
    unavailable: dict[str, str]


class StatusOut(BaseModel):
    connection: ConnectionOut
    latest: SnapshotOut | None
    uptime_24h: float | None


class HistoryPoint(BaseModel):
    at: datetime
    state: str | None = None
    latency_ms: float | None
    health_score: float | None
    up_checks: int
    total_checks: int


class UptimeOut(BaseModel):
    window: str
    uptime_pct: float | None
    checks: int
    bars: list[dict[str, Any]]  # per bucket: {start, uptime_pct, checks}


class OverviewOut(BaseModel):
    total: int
    by_state: dict[str, int]
    worst: list[ConnectionOut]
    egress_ips: list[str]


class CheckQueued(BaseModel):
    job_id: int


class GrantScript(BaseModel):
    engine: str
    script: str
