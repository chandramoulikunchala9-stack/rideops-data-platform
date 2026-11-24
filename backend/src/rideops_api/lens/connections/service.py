"""Connection manager (§10.1): draft resolution, staged tests, encrypted storage, demo seeding."""

import json
import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import psycopg

from rideops_api.lens import pools
from rideops_api.lens.adapters.base import (
    ConnectionConfig,
    Secret,
    StageResult,
    Stopwatch,
    TestResult,
)
from rideops_api.lens.adapters.registry import AdapterUnavailable, get_adapter_class
from rideops_api.lens.connections import url_parser
from rideops_api.lens.security import netguard
from rideops_api.lens.security.crypto import KeyRing, SecretBlob, aad_for
from rideops_api.settings import LensSettings, allowed_port_set

DEMO_NAME = "RideOps demo"
logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _keyring(value: str, active: int | None) -> KeyRing:
    return KeyRing.from_env_value(value, active)


def keyring_for(settings: LensSettings) -> KeyRing:
    """Raises KeyRingError when LENS_MASTER_KEYS is missing or malformed."""
    raw = settings.lens_master_keys.get_secret_value() if settings.lens_master_keys else ""
    return _keyring(raw, settings.lens_active_key_version)


@dataclass
class Draft:
    """A resolved, validated connection config plus its secret (never persisted in clear)."""

    engine: str
    host: str
    port: int
    database: str | None
    username: str | None
    options: dict[str, str]
    tls_mode: str
    secret: Secret

    @property
    def display_url(self) -> str:
        return url_parser.build_display_url(self.engine, self.host, self.port, self.database, self.username,
                                            self.options)  # fmt: skip

    def fingerprint_data(self) -> dict[str, Any]:
        return {"engine": self.engine, "host": self.host, "port": self.port, "database": self.database,
                "username": self.username, "options": self.options, "tls_mode": self.tls_mode,
                "secret": self.secret.to_dict()}  # fmt: skip


def resolve_draft(fields: dict[str, Any], secret: dict[str, Any]) -> Draft:
    """URL and/or form fields → Draft. Form fields are rebuilt into a URL and parsed, so both
    paths go through the same validation (option allowlist, host normalisation)."""
    url = fields.get("url")
    if not url and fields.get("engine") == "sqlite":
        try:  # SQLite = an uploaded file; `database` carries the upload id
            upload_id = str(uuid.UUID(str(fields.get("database") or "")))
        except ValueError:
            raise url_parser.UrlError(
                "URL_INVALID", "Upload the SQLite file first, then use its upload id."
            ) from None
        return Draft(engine="sqlite", host="upload", port=0, database=upload_id, username=None, options={},
                     tls_mode="disable", secret=Secret())  # fmt: skip
    if url:
        parsed = url_parser.parse(url)
    else:
        engine = fields.get("engine")
        if not engine or not fields.get("host"):
            raise url_parser.UrlError(
                "URL_INVALID", "Give either a URL or at least an engine and a host."
            )
        if engine not in url_parser.CANONICAL_SCHEME:
            raise url_parser.UrlError(
                "ENGINE_UNSUPPORTED", "This database type isn't supported by URL/form."
            )
        host = url_parser.normalize_host(str(fields["host"]))
        parsed = url_parser.parse(url_parser.build_display_url(
            engine, host, fields.get("port") or url_parser.DEFAULT_PORTS[engine], fields.get("database"),
            fields.get("username"), fields.get("options") or {}))  # fmt: skip
    for key in ("host", "port", "database", "username"):
        if url and fields.get(key) not in (None, ""):
            value = fields[key]
            setattr(parsed, key, url_parser.normalize_host(str(value)) if key == "host" else value)
    tls_mode = fields.get("tls_mode") or parsed.tls_mode
    if tls_mode not in url_parser.TLS_MODES:
        raise url_parser.UrlError("URL_INVALID", f"Unknown TLS mode {tls_mode!r}.")
    secret_obj = Secret.from_dict({**secret, "password": secret.get("password") or parsed.password})
    return Draft(engine=parsed.engine, host=parsed.host, port=parsed.port, database=parsed.database,
                 username=parsed.username, options=parsed.options, tls_mode=tls_mode, secret=secret_obj)  # fmt: skip


def _policy_kwargs(workspace_settings: dict[str, Any], settings: LensSettings) -> dict[str, Any]:
    return {
        "allow_private_networks": settings.allow_private_networks,
        "allowed_ports": allowed_port_set(settings)
        | {int(p) for p in workspace_settings.get("allowed_ports", [])},
        "host_allowlist": workspace_settings.get("egress_allowlist", []),
    }


def upload_path(settings: LensSettings, workspace_id: Any, upload_id: Any) -> str:
    """Server-chosen path of an uploaded SQLite file; ids are UUIDs, so no user text reaches the path."""
    import os

    return os.path.join(
        settings.uploads_dir,
        str(uuid.UUID(str(workspace_id))),
        f"{uuid.UUID(str(upload_id))}.sqlite",
    )


def guard_target(engine: str, host: str, port: int, options: dict[str, Any], workspace_settings: dict[str, Any],
                 settings: LensSettings) -> netguard.GuardResult:  # fmt: skip
    policy = _policy_kwargs(workspace_settings, settings)
    if engine == "mongodb" and str(options.get("srv", "")).lower() == "true":
        return netguard.check_srv(host, **policy)
    return netguard.check(host, port, **policy)


def run_test(
    draft: Draft,
    *,
    workspace_settings: dict[str, Any],
    settings: LensSettings,
    connection_id: str | None = None,
    trusted: bool = False,
    workspace_id: Any = None,
) -> TestResult:
    """DNS → NETWORK_POLICY (here) → TCP → TLS → AUTH → DB_SELECT → PRIVILEGES → CAPABILITIES (adapter).
    `trusted` skips the Network Guard for the operator-configured demo connection only."""
    adapter_cls = get_adapter_class(draft.engine)  # AdapterUnavailable propagates to the API
    stages: list[StageResult] = []
    if draft.engine == "sqlite":  # a local, read-only file: no network, TLS or login stages
        cfg = ConnectionConfig(connection_id=connection_id, engine="sqlite", host=None, port=None,
                               database=draft.database, username=None,
                               options={"path": upload_path(settings, workspace_id, draft.database)},
                               tls_mode="disable", pinned_ip=None, timeout_s=settings.health_timeout_s)  # fmt: skip
        return adapter_cls(cfg, draft.secret).test()
    if draft.tls_mode == "disable" and not workspace_settings.get(
        "allow_insecure_connections", False
    ):
        stages.append(StageResult("TLS", False, 0, "Insecure connections are disabled for this workspace. "
                                  "An owner can allow them in workspace settings.", "CONN_TLS_REQUIRED"))  # fmt: skip
        return TestResult(False, stages)
    pinned = None
    if not trusted:
        sw = Stopwatch()
        try:
            guard = guard_target(
                draft.engine, draft.host, draft.port, draft.options, workspace_settings, settings
            )
        except netguard.NetworkPolicyError as err:
            if err.stage == "DNS":
                stages.append(StageResult("DNS", False, sw.ms, err.message, err.code))
            else:
                stages.append(StageResult("DNS", True, sw.ms, ""))
                stages.append(StageResult("NETWORK_POLICY", False, 0, err.message, err.code))
            return TestResult(False, stages)
        pinned = guard.pinned_ip
        stages.append(
            StageResult("DNS", True, sw.ms, f"{draft.host} → {', '.join(guard.addresses[:3])}")
        )
        stages.append(
            StageResult(
                "NETWORK_POLICY",
                True,
                0,
                "Private network allowed by policy" if guard.private_allowed else "Public address",
            )
        )
    cfg = ConnectionConfig(connection_id=connection_id, engine=draft.engine, host=draft.host, port=draft.port,
                           database=draft.database, username=draft.username, options=draft.options,
                           tls_mode=draft.tls_mode, pinned_ip=pinned, timeout_s=settings.health_timeout_s)  # fmt: skip
    with pools.target_slot(
        connection_id or f"draft:{draft.host}:{draft.port}", settings.max_target_sessions
    ):
        result = adapter_cls(cfg, draft.secret).test()
    result.stages = stages + result.stages
    return result


def record_test(conn: psycopg.Connection, *, workspace_id: Any, connection_id: Any, user_id: Any,
                result: TestResult, fingerprint: str) -> str:  # fmt: skip
    row = conn.execute(
        """INSERT INTO app.connection_tests (workspace_id, connection_id, ok, stages, privilege_findings,
                                             server_version, capabilities, config_fingerprint, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (workspace_id, connection_id, result.ok, json.dumps([s.as_dict() for s in result.stages]),
         json.dumps(result.privilege_findings), result.server_version, sorted(result.capabilities),
         fingerprint, user_id),
    ).fetchone()  # fmt: skip
    return str(row["id"])


def store_secret(conn: psycopg.Connection, keyring: KeyRing, workspace_id: Any, connection_id: Any,
                 secret: Secret) -> None:  # fmt: skip
    """Insert or replace (rotation) the encrypted secret. Replacing the row destroys the old DEK."""
    blob = keyring.encrypt(secret.to_dict(), aad_for(workspace_id, connection_id))
    conn.execute(
        """INSERT INTO app.connection_secrets (connection_id, kek_version, wrapped_dek, nonce, ciphertext)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (connection_id) DO UPDATE
             SET kek_version = EXCLUDED.kek_version, wrapped_dek = EXCLUDED.wrapped_dek,
                 nonce = EXCLUDED.nonce, ciphertext = EXCLUDED.ciphertext, rotated_at = now()""",
        (connection_id, blob.kek_version, blob.wrapped_dek, blob.nonce, blob.ciphertext),
    )


def load_secret(
    conn: psycopg.Connection, keyring: KeyRing, workspace_id: Any, connection_id: Any
) -> Secret | None:
    row = conn.execute(
        "SELECT * FROM app.connection_secrets WHERE connection_id = %s", (connection_id,)
    ).fetchone()
    if row is None:
        return None
    blob = SecretBlob(
        row["kek_version"], bytes(row["wrapped_dek"]), bytes(row["nonce"]), bytes(row["ciphertext"])
    )
    return Secret.from_dict(keyring.decrypt(blob, aad_for(workspace_id, connection_id)))


def draft_from_row(row: dict[str, Any], secret: Secret) -> Draft:
    return Draft(
        engine=row["engine"],
        host=row["host"],
        port=row["port"],
        database=row["database"],
        username=row["username"],
        options=row["options"] or {},
        tls_mode=row["tls_mode"],
        secret=secret,
    )


def insert_connection(conn: psycopg.Connection, *, workspace_id: Any, draft: Draft, name: str, environment: str,
                      tags: list[str], color: str | None, check_interval_s: int, created_by: Any,
                      capabilities: list[str], server_version: str | None, keyring: KeyRing,
                      is_demo: bool = False, connection_id: Any = None) -> dict[str, Any]:  # fmt: skip
    connection_id = connection_id or uuid.uuid4()
    row = conn.execute(
        """INSERT INTO app.connections (id, workspace_id, name, engine, environment, host, port, database,
                 username, display_url, options, tls_mode, tags, color, check_interval_s, is_demo,
                 capabilities, server_version, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (connection_id, workspace_id, name, draft.engine, environment, draft.host, draft.port, draft.database,
         draft.username, draft.display_url, json.dumps(draft.options), draft.tls_mode, tags, color,
         check_interval_s, is_demo, capabilities, server_version, created_by),
    ).fetchone()  # fmt: skip
    store_secret(conn, keyring, workspace_id, connection_id, draft.secret)
    set_schedule(conn, connection_id, check_interval_s)
    if not is_demo:  # FR-40 default rule pack (the shared demo would only add noise)
        from rideops_api.lens.alerts.engine import default_rules  # local: avoid an import cycle

        default_rules(conn, workspace_id, connection_id, created_by)
    return row


# Recurring work per connection besides health checks (§10.5 job table).
CATALOG_SCHEDULES = {"schema_sync": 86_400, "stats_refresh": 3_600}


def set_schedule(conn: psycopg.Connection, connection_id: Any, interval_s: int) -> None:
    """Health check every `interval_s`, plus the daily schema sync and hourly stats refresh."""
    for job_type, every in {"health_check": interval_s, **CATALOG_SCHEDULES}.items():
        conn.execute(
            """INSERT INTO app.schedules (connection_id, job_type, interval_s, next_run_at)
               VALUES (%s, %s, %s, now() + make_interval(secs => %s))
               ON CONFLICT (connection_id, job_type) DO UPDATE
                 SET interval_s = EXCLUDED.interval_s,
                     next_run_at = LEAST(app.schedules.next_run_at, EXCLUDED.next_run_at)""",
            (connection_id, job_type, every, every),
        )


def enqueue_initial_jobs(conn: psycopg.Connection, connection_id: Any, workspace_id: Any) -> None:
    """First health check and catalog sync right after a connection is saved (§14.1)."""
    from rideops_api.jobs import queue  # local: the queue imports the job registry

    for job_type in ("health_check", "schema_sync"):
        queue.enqueue(conn, job_type, connection_id=connection_id, workspace_id=workspace_id,
                      idempotency_key=f"{job_type}:{connection_id}:initial")  # fmt: skip


class TargetUnavailable(Exception):
    """A stored connection can't be opened right now (paused, deleted, no secret, blocked)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def load_target(db: Any, connection_id: Any, settings: LensSettings):
    """Stored connection → (row, adapter) ready to use. Applies the same checks as a health check:
    not paused/deleted, secret decryptable, Network Guard (except the trusted demo), TLS policy."""
    from rideops_api.lens.security.crypto import DecryptError, KeyRingError

    with db.pool.connection() as conn:
        row = conn.execute(
            """SELECT c.*, w.settings AS ws_settings FROM app.connections c
                 JOIN app.workspaces w ON w.id = c.workspace_id
                WHERE c.id = %s AND c.deleted_at IS NULL AND w.deleted_at IS NULL""",
            (connection_id,),
        ).fetchone()
        if row is None:
            raise TargetUnavailable("NOT_FOUND", "Connection not found.")
        if row["is_paused"]:
            raise TargetUnavailable("CONNECTION_PAUSED", "Resume the connection first.")
        try:
            secret = load_secret(conn, keyring_for(settings), row["workspace_id"], row["id"])
        except (KeyRingError, DecryptError) as exc:
            raise TargetUnavailable("SECRET_UNAVAILABLE", str(exc)) from exc
    if secret is None:
        raise TargetUnavailable("SECRET_UNAVAILABLE", "This connection has no stored credentials.")
    try:
        adapter_cls = get_adapter_class(row["engine"])
    except AdapterUnavailable as exc:
        raise TargetUnavailable(exc.code, exc.message) from exc
    return row, adapter_cls(target_config(row, row["ws_settings"], settings), secret)


def target_config(
    row: dict[str, Any], workspace_settings: dict[str, Any], settings: LensSettings
) -> ConnectionConfig:
    """Stored connection row → adapter config, applying TLS policy and the Network Guard.
    Raises TargetUnavailable with a catalogue code when policy forbids contacting the target."""
    options = dict(row["options"] or {})
    pinned = None
    if row["engine"] == "sqlite":
        options["path"] = upload_path(settings, row["workspace_id"], row["database"])
    else:
        if row["tls_mode"] == "disable" and not workspace_settings.get(
            "allow_insecure_connections", False
        ):
            raise TargetUnavailable(
                "CONN_TLS_REQUIRED", "Insecure connections are disabled for this workspace."
            )
        if not row["is_demo"]:
            try:
                pinned = guard_target(row["engine"], row["host"], row["port"], options, workspace_settings,
                                      settings).pinned_ip or None  # fmt: skip
            except netguard.NetworkPolicyError as err:
                raise TargetUnavailable(err.code, err.message) from err
    return ConnectionConfig(
        connection_id=str(row["id"]),
        engine=row["engine"],
        host=row["host"],
        port=row["port"],
        database=row["database"],
        username=row["username"],
        options=options,
        tls_mode=row["tls_mode"],
        pinned_ip=pinned,
        timeout_s=settings.health_timeout_s,
    )


def seed_demo_connection(conn: psycopg.Connection, workspace_id: Any, settings: LensSettings,
                         created_by: Any = None) -> dict[str, Any] | None:  # fmt: skip
    """F-CON-11: give a workspace the read-only RideOps demo connection, if the operator configured
    RIDEOPS_DEMO_URL and the key ring. Idempotent. Trusted (operator-provided), so no Network Guard."""
    if settings.rideops_demo_url is None or settings.lens_master_keys is None:
        return None
    exists = conn.execute(
        "SELECT 1 FROM app.connections WHERE workspace_id = %s AND is_demo AND deleted_at IS NULL",
        (workspace_id,),
    ).fetchone()
    if exists:
        return None
    draft = resolve_draft({"url": settings.rideops_demo_url.get_secret_value()}, {})
    from rideops_api.lens.adapters.base import Capability  # local: avoid widening module imports

    caps = sorted(get_adapter_class(draft.engine).capabilities - {Capability.SLOW_QUERIES})
    row = insert_connection(
        conn,
        workspace_id=workspace_id,
        draft=draft,
        name=DEMO_NAME,
        environment="demo",
        tags=["demo"],
        color=None,
        check_interval_s=300,
        created_by=created_by,
        capabilities=caps,
        server_version=None,
        keyring=keyring_for(settings),
        is_demo=True,
    )
    enqueue_initial_jobs(conn, row["id"], workspace_id)
    return row


def seed_demo_safely(
    conn: psycopg.Connection, workspace_id: Any, settings: LensSettings, created_by: Any
) -> None:
    """Signup and workspace creation must not fail because the demo is misconfigured."""
    try:
        with conn.transaction():  # savepoint: a failure here leaves the outer transaction intact
            seed_demo_connection(conn, workspace_id, settings, created_by)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not seed the RideOps demo connection for workspace %s", workspace_id
        )


__all__ = ["AdapterUnavailable", "Draft", "resolve_draft", "run_test", "seed_demo_connection"]
