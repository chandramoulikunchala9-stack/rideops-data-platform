"""Multi-database adapter interface (§11).

Deviation from the design doc: methods are synchronous. The API and the worker are synchronous
(psycopg 3 sync pools, FastAPI threadpool for `def` endpoints), so `async` would only add an
event-loop hop around blocking drivers. Concurrency per target is bounded by `pools.target_slot`.
Later phases add catalog/profiling/query methods to this same class.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, ClassVar


class Engine(StrEnum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    MSSQL = "mssql"
    SQLITE = "sqlite"
    MONGODB = "mongodb"
    SNOWFLAKE = "snowflake"
    BIGQUERY = "bigquery"
    DATABRICKS = "databricks"


class Capability(StrEnum):
    STATUS_BASIC = "status_basic"
    SESSIONS = "sessions"
    LONG_QUERIES = "long_queries"
    LOCKS = "locks"
    CACHE_HIT = "cache_hit"
    REPLICATION = "replication"
    SLOW_QUERIES = "slow_queries"
    SCHEMA = "schema"
    TABLE_STATS = "table_stats"
    INDEX_USAGE = "index_usage"
    COLUMN_PROFILE = "column_profile"
    QUERY_CONSOLE = "query_console"
    EXPLAIN = "explain"
    PRIVILEGE_CHECK = "privilege_check"


STAGES = ("DNS", "NETWORK_POLICY", "TCP", "TLS", "AUTH", "DB_SELECT", "PRIVILEGES", "CAPABILITIES")


@dataclass(frozen=True)
class ConnectionConfig:
    connection_id: str | None
    engine: Engine
    host: str | None
    port: int | None
    database: str | None
    username: str | None
    options: dict[str, Any]
    tls_mode: str
    pinned_ip: str | None
    timeout_s: int = 10


@dataclass(frozen=True)
class Secret:
    password: str | None = field(default=None, repr=False)
    ca_cert_pem: str | None = field(default=None, repr=False)
    client_cert_pem: str | None = field(default=None, repr=False)
    client_key_pem: str | None = field(default=None, repr=False)
    extra: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Secret":
        data = data or {}
        return cls(password=data.get("password"), ca_cert_pem=data.get("ca_cert_pem"),
                   client_cert_pem=data.get("client_cert_pem"), client_key_pem=data.get("client_key_pem"),
                   extra=dict(data.get("extra") or {}))  # fmt: skip

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {"password": self.password, "ca_cert_pem": self.ca_cert_pem,
                                  "client_cert_pem": self.client_cert_pem, "client_key_pem": self.client_key_pem,
                                  "extra": self.extra or None}.items() if v is not None}  # fmt: skip


class AdapterError(Exception):
    """A failure attributable to the target, classified by stage and catalogue code (§22)."""

    def __init__(self, stage: str, code: str, message: str) -> None:
        super().__init__(message)
        self.stage, self.code, self.message = stage, code, message


@dataclass
class StageResult:
    stage: str
    ok: bool
    ms: int
    message: str = ""
    code: str | None = None
    warning: bool = False  # e.g. PRIV_WRITE_GRANTED: reported, but does not fail the test

    def as_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "ok": self.ok, "ms": self.ms, "message": self.message,
                "code": self.code, "warning": self.warning}  # fmt: skip


@dataclass
class TestResult:
    ok: bool
    stages: list[StageResult]
    server_version: str | None = None
    capabilities: set[Capability] = field(default_factory=set)
    privilege_findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def failure(self) -> StageResult | None:
        return next((s for s in self.stages if not s.ok and not s.warning), None)


@dataclass
class StatusSnapshot:
    taken_at: datetime
    reachable: bool
    latency_ms: int | None
    server_version: str | None = None
    uptime_s: int | None = None
    db_size_bytes: int | None = None
    metrics: dict[str, float | int | None] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    error_stage: str | None = None


class Stopwatch:
    def __init__(self) -> None:
        self.started = time.perf_counter()

    @property
    def ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)


def tcp_stage(cfg: "ConnectionConfig", default_port: int) -> "StageResult":
    """TCP reachability of the pinned IP (or host), shared by every network adapter."""
    import socket

    sw = Stopwatch()
    target = cfg.pinned_ip or cfg.host
    port = cfg.port or default_port
    try:
        with socket.create_connection((target, port), timeout=cfg.timeout_s):
            pass
    except TimeoutError:
        return StageResult("TCP", False, sw.ms, "Timed out. Is the database public and is our IP allowlisted?",
                           "CONN_TIMEOUT")  # fmt: skip
    except OSError as exc:
        return StageResult("TCP", False, sw.ms, f"Connection refused or unreachable ({exc.strerror or exc}).",
                           "CONN_REFUSED")  # fmt: skip
    return StageResult("TCP", True, sw.ms, f"{target}:{port}")


def now() -> datetime:
    return datetime.now(timezone.utc)


class DatabaseAdapter:
    """Base class. Subclasses set the ClassVars and implement the methods."""

    engine: ClassVar[Engine]
    capabilities: ClassVar[frozenset[Capability]]
    default_port: ClassVar[int | None]

    def __init__(self, cfg: ConnectionConfig, secret: Secret) -> None:
        self.cfg, self.secret = cfg, secret

    def test(self) -> TestResult:  # stages TCP..CAPABILITIES (DNS + policy run in the service)
        raise NotImplementedError

    def status(self) -> StatusSnapshot:
        raise NotImplementedError

    def privilege_findings(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    # -- analytics (Phase 13): every method takes the open, read-only session from connect() --
    def connect(self):  # context manager yielding an engine session
        raise NotImplementedError

    def list_tables(self, s) -> list[dict[str, Any]]:
        raise NotImplementedError

    def table_stats(self, s) -> list[dict[str, Any]]:
        raise NotImplementedError

    def freshness_max(self, s, schema: str, table: str, column: str) -> Any:
        raise NotImplementedError

    def exact_count(self, s, schema: str, table: str) -> int:
        raise NotImplementedError

    def sample(self, s, schema: str, table: str, columns: list[str], n: int,
               row_estimate: int | None) -> tuple[list[dict[str, Any]], str]:  # fmt: skip
        raise NotImplementedError

    def preview(
        self, s, schema: str, table: str, limit: int = 50
    ) -> tuple[list[str], list[list[Any]]]:
        raise NotImplementedError

    def run_readonly(self, s, statement: str, *, wrap: bool, max_rows: int, max_bytes: int,
                     timeout_s: int) -> dict[str, Any]:  # fmt: skip
        raise NotImplementedError

    def explain(self, s, statement: str) -> Any:
        raise NotImplementedError

    @staticmethod
    def readonly_grant_script(database: str, username: str) -> str:
        raise NotImplementedError
