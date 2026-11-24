"""PostgreSQL adapter: staged connection test, read-only sessions, status metrics (§15.4),
privilege findings and the read-only grant script (§18.5)."""

import os
import re
import socket
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ClassVar

import psycopg
from psycopg.rows import dict_row

from rideops_api.lens.adapters import pg_catalog
from rideops_api.lens.adapters.base import (
    AdapterError,
    Capability,
    DatabaseAdapter,
    Engine,
    StageResult,
    StatusSnapshot,
    Stopwatch,
    TestResult,
    now,
)

# Session hardening, layer 2 of 3 (§18.5): sent as startup options, so it applies before any query.
SESSION_OPTIONS = (
    "-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=2000 "
    "-c idle_in_transaction_session_timeout=60000"
)
LONG_QUERY_MINUTES = 5
QUERY_TEXT_MAX = 300

_VERSION = re.compile(r"\d+(?:\.\d+)*")
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_NUMBER_LITERAL = re.compile(r"\b\d+(?:\.\d+)?\b")


def mask_query(text: str | None) -> str | None:
    """Truncate and mask literals before anything leaves the target (§18.7)."""
    if text is None:
        return None
    masked = _NUMBER_LITERAL.sub("?", _STRING_LITERAL.sub("'…'", text))
    return masked[:QUERY_TEXT_MAX] + ("…" if len(masked) > QUERY_TEXT_MAX else "")


def short_version(raw: str) -> str:
    """'16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)' → 'PostgreSQL 16.15'."""
    match = _VERSION.match(raw or "")
    return f"PostgreSQL {match.group(0) if match else raw}"


def classify(exc: Exception) -> AdapterError:
    """Map a libpq error to a stage + catalogue code (§22)."""
    msg = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    low = str(exc).lower()
    if "server does not support ssl" in low or "no encryption" in low:
        return AdapterError(
            "TLS", "CONN_TLS_REQUIRED", "The server and Lens disagree on TLS: " + msg
        )
    if "certificate" in low or "ssl" in low and ("verify" in low or "handshake" in low):
        return AdapterError(
            "TLS", "CONN_TLS_VERIFY_FAILED", "Certificate not trusted or host name mismatch: " + msg
        )
    if (
        "password authentication failed" in low
        or "authentication failed" in low
        or ("role" in low and "does not exist" in low)
    ):
        return AdapterError("AUTH", "CONN_AUTH_FAILED", "Wrong username/password or auth method.")
    if "pg_hba.conf" in low:
        return AdapterError(
            "AUTH",
            "CONN_AUTH_FAILED",
            "The server rejected this user/host combination (pg_hba.conf).",
        )
    if "database" in low and "does not exist" in low:
        return AdapterError(
            "DB_SELECT", "CONN_DB_NOT_FOUND", "Database doesn't exist or no CONNECT privilege."
        )
    if "permission denied for database" in low:
        return AdapterError(
            "DB_SELECT", "CONN_DB_NOT_FOUND", "No CONNECT privilege on this database."
        )
    if "too many" in low and "connections" in low or "remaining connection slots" in low:
        return AdapterError("AUTH", "CONN_TOO_MANY", "The server has no free connections.")
    if "the database system is starting up" in low or "shutting down" in low:
        return AdapterError("AUTH", "CONN_DOWN", msg)
    if "timeout" in low:
        return AdapterError(
            "TCP", "CONN_TIMEOUT", "Timed out. Is the database public and is our IP allowlisted?"
        )
    if "refused" in low:
        return AdapterError("TCP", "CONN_REFUSED", "Connection refused on that port.")
    return AdapterError("AUTH", "CONN_ERROR", msg)


class PostgresAdapter(DatabaseAdapter):
    engine: ClassVar[Engine] = Engine.POSTGRES
    default_port: ClassVar[int | None] = 5432
    capabilities: ClassVar[frozenset[Capability]] = frozenset({
        Capability.STATUS_BASIC, Capability.SESSIONS, Capability.LONG_QUERIES, Capability.LOCKS,
        Capability.CACHE_HIT, Capability.REPLICATION, Capability.SLOW_QUERIES, Capability.SCHEMA,
        Capability.TABLE_STATS, Capability.INDEX_USAGE, Capability.COLUMN_PROFILE,
        Capability.QUERY_CONSOLE, Capability.EXPLAIN, Capability.PRIVILEGE_CHECK,
    })  # fmt: skip

    # ----------------------------------------------------------------- connection
    @contextmanager
    def _ca_file(self) -> Iterator[str | None]:
        """Uploaded CA → private temp file (0600), deleted after use. No user-supplied paths."""
        if not self.secret.ca_cert_pem:
            yield None
            return
        fd, path = tempfile.mkstemp(prefix="lens-ca-", suffix=".pem")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(self.secret.ca_cert_pem)
            os.chmod(path, 0o600)
            yield path
        finally:
            os.unlink(path)

    def _kwargs(self, ca_path: str | None) -> dict[str, Any]:
        cfg = self.cfg
        kwargs: dict[str, Any] = {
            "host": cfg.host,  # kept for TLS SNI + certificate host name verification
            "port": cfg.port or 5432,
            "dbname": cfg.database or "postgres",
            "user": cfg.username,
            "password": self.secret.password or "",
            "sslmode": cfg.tls_mode,
            "connect_timeout": max(
                2, min(cfg.timeout_s, int(cfg.options.get("connect_timeout", cfg.timeout_s)))
            ),
            "application_name": "rideops-lens",
            "options": SESSION_OPTIONS,
        }
        if cfg.pinned_ip:
            kwargs["hostaddr"] = cfg.pinned_ip  # connect to the IP the Network Guard approved
        if cfg.tls_mode in ("verify-ca", "verify-full"):
            kwargs["sslrootcert"] = ca_path or "system"
        if cfg.options.get("target_session_attrs"):
            kwargs["target_session_attrs"] = cfg.options["target_session_attrs"]
        return kwargs

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        with self._ca_file() as ca_path:
            try:
                conn = psycopg.connect(
                    **self._kwargs(ca_path), autocommit=True, row_factory=dict_row
                )
            except psycopg.Error as exc:
                raise classify(exc) from exc
        try:
            conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            yield conn
        finally:
            conn.close()

    # ----------------------------------------------------------------------- test
    def _tcp(self) -> StageResult:
        sw = Stopwatch()
        target = self.cfg.pinned_ip or self.cfg.host
        try:
            with socket.create_connection(
                (target, self.cfg.port or 5432), timeout=self.cfg.timeout_s
            ):
                pass
        except TimeoutError:
            return StageResult("TCP", False, sw.ms, "Timed out. Is the database public and is our IP allowlisted?",
                               "CONN_TIMEOUT")  # fmt: skip
        except OSError as exc:
            return StageResult("TCP", False, sw.ms, f"Connection refused or unreachable ({exc.strerror or exc}).",
                               "CONN_REFUSED")  # fmt: skip
        return StageResult("TCP", True, sw.ms, f"{target}:{self.cfg.port}")

    def test(self) -> TestResult:
        stages: list[StageResult] = []
        tcp = self._tcp()
        stages.append(tcp)
        if not tcp.ok:
            return TestResult(False, stages)
        sw = Stopwatch()
        try:
            with self.connect() as conn:
                connect_ms = sw.ms
                ssl_on = conn.pgconn.ssl_in_use
                tls_msg = f"TLS on ({self.cfg.tls_mode})" if ssl_on else "TLS disabled (insecure)"
                stages.append(StageResult("TLS", True, 0, tls_msg, warning=not ssl_on))
                stages.append(
                    StageResult("AUTH", True, connect_ms, f"Signed in as {self.cfg.username}")
                )

                sw = Stopwatch()
                row = conn.execute("SELECT current_database() AS db, version() AS version, "
                                   "current_setting('server_version') AS short").fetchone()  # fmt: skip
                stages.append(StageResult("DB_SELECT", True, sw.ms, f"Database {row['db']}"))
                version = short_version(row["short"])

                sw = Stopwatch()
                findings = self._privilege_findings(conn)
                write = [f for f in findings if f["severity"] == "warning"]
                if write:
                    kinds = sorted({f["privilege"] for f in write})
                    stages.append(StageResult("PRIVILEGES", False, sw.ms,
                                              f"Role can write ({', '.join(kinds)}). Use a read-only role.",
                                              "PRIV_WRITE_GRANTED", warning=True))  # fmt: skip
                elif any(f["privilege"] == "PG_MONITOR_MISSING" for f in findings):
                    stages.append(StageResult("PRIVILEGES", True, sw.ms,
                                              "Read-only. Some metrics need GRANT pg_monitor.",
                                              "PRIV_INSUFFICIENT", warning=True))  # fmt: skip
                else:
                    stages.append(StageResult("PRIVILEGES", True, sw.ms, "Read-only role"))

                sw = Stopwatch()
                caps = self._detect_capabilities(conn)
                stages.append(StageResult("CAPABILITIES", True, sw.ms, f"{len(caps)} capabilities"))
                return TestResult(True, stages, version, caps, findings)
        except AdapterError as err:
            if err.stage == "TLS":
                stages.append(StageResult("TLS", False, sw.ms, err.message, err.code))
            elif err.stage == "DB_SELECT":
                stages += [StageResult("TLS", True, 0, ""), StageResult("AUTH", True, sw.ms, ""),
                           StageResult("DB_SELECT", False, 0, err.message, err.code)]  # fmt: skip
            else:
                stages += [
                    StageResult("TLS", True, 0, ""),
                    StageResult("AUTH", False, sw.ms, err.message, err.code),
                ]
            return TestResult(False, stages)

    # ------------------------------------------------------------------ privileges
    def _privilege_findings(self, conn: psycopg.Connection) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        role = conn.execute(
            """SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls,
                      pg_has_role(current_user, 'pg_monitor', 'MEMBER') AS monitor,
                      pg_has_role(current_user, 'pg_write_all_data', 'MEMBER') AS write_all
                 FROM pg_roles WHERE rolname = current_user"""
        ).fetchone()
        if role["rolsuper"]:
            return [{"privilege": "SUPERUSER", "object": "*", "severity": "warning"}]
        for attr, name in (("rolcreaterole", "CREATEROLE"), ("rolcreatedb", "CREATEDB"),
                           ("rolbypassrls", "BYPASSRLS"), ("write_all", "PG_WRITE_ALL_DATA")):  # fmt: skip
            if role[attr]:
                findings.append({"privilege": name, "object": "*", "severity": "warning"})
        rows = conn.execute(
            """SELECT n.nspname AS schema, p.privilege, count(*) AS tables
                 FROM pg_class c
                 JOIN pg_namespace n ON n.oid = c.relnamespace
                 CROSS JOIN unnest(ARRAY['INSERT','UPDATE','DELETE','TRUNCATE']) AS p(privilege)
                WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND n.nspname NOT LIKE 'pg_toast%%'
                  AND has_table_privilege(current_user, c.oid, p.privilege)
                GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 100"""
        ).fetchall()
        for r in rows:
            findings.append({"privilege": r["privilege"], "object": f"{r['schema']}.*", "tables": r["tables"],
                             "severity": "warning"})  # fmt: skip
        schemas = conn.execute(
            """SELECT nspname FROM pg_namespace
                WHERE nspname NOT IN ('pg_catalog', 'information_schema') AND nspname NOT LIKE 'pg_%%'
                  AND has_schema_privilege(current_user, oid, 'CREATE')"""
        ).fetchall()
        for s in schemas:
            findings.append({"privilege": "CREATE", "object": s["nspname"], "severity": "warning"})
        if not role["monitor"]:
            findings.append({"privilege": "PG_MONITOR_MISSING", "object": "*", "severity": "info"})
        return findings

    def privilege_findings(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return self._privilege_findings(conn)

    def _detect_capabilities(self, conn: psycopg.Connection) -> set[Capability]:
        caps = set(self.capabilities)
        has_pgss = conn.execute(
            "SELECT to_regclass('pg_stat_statements') IS NOT NULL AS ok"
        ).fetchone()["ok"]
        if not has_pgss:
            caps.discard(Capability.SLOW_QUERIES)
        return caps

    # --------------------------------------------------------------------- status
    def status(self) -> StatusSnapshot:
        sw = Stopwatch()
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1")
                latency = sw.ms
                return self._collect(conn, latency)
        except AdapterError as err:
            return StatusSnapshot(taken_at=now(), reachable=False, latency_ms=None, error_code=err.code,
                                  error_message=err.message, error_stage=err.stage)  # fmt: skip
        except psycopg.errors.QueryCanceled as exc:
            return StatusSnapshot(taken_at=now(), reachable=True, latency_ms=sw.ms, error_code="QUERY_TIMEOUT",
                                  error_message=str(exc).splitlines()[0], error_stage="STATUS")  # fmt: skip

    def _collect(self, conn: psycopg.Connection, latency_ms: int) -> StatusSnapshot:
        unavailable: dict[str, str] = {}
        base = conn.execute(
            """SELECT current_setting('server_version') AS version,
                      extract(epoch FROM now() - pg_postmaster_start_time())::bigint AS uptime_s,
                      pg_database_size(current_database()) AS size,
                      pg_is_in_recovery() AS in_recovery,
                      pg_has_role(current_user, 'pg_monitor', 'MEMBER')
                        OR (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS monitor"""
        ).fetchone()
        sessions = conn.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE state = 'active') AS active,
                      count(*) FILTER (WHERE state = 'idle') AS idle,
                      current_setting('max_connections')::int AS max
                 FROM pg_stat_activity WHERE backend_type = 'client backend'"""
        ).fetchone()
        long_running = conn.execute(
            """SELECT pid, usename, extract(epoch FROM now() - query_start)::int AS runtime_s, query
                 FROM pg_stat_activity
                WHERE state = 'active' AND backend_type = 'client backend' AND pid <> pg_backend_pid()
                  AND now() - query_start > make_interval(mins => %s)
                ORDER BY query_start LIMIT 10""",
            (LONG_QUERY_MINUTES,),
        ).fetchall()
        blocked = conn.execute(
            "SELECT count(*) AS n FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0"
        ).fetchone()["n"]
        db = conn.execute(
            """SELECT blks_hit::float / nullif(blks_hit + blks_read, 0) AS hit, deadlocks, xact_commit, xact_rollback
                 FROM pg_stat_database WHERE datname = current_database()"""
        ).fetchone()
        xid = conn.execute(
            "SELECT max(age(datfrozenxid))::float / 2000000000 AS pct FROM pg_database"
        ).fetchone()
        lag = None
        if base["in_recovery"]:
            lag = conn.execute(
                "SELECT extract(epoch FROM now() - pg_last_xact_replay_timestamp())::float AS lag"
            ).fetchone()["lag"]

        if not base["monitor"]:
            unavailable["queries.long_running.text"] = (
                "needs pg_monitor role (GRANT pg_monitor TO <user>)"
            )
        used_pct = (
            round(100.0 * sessions["total"] / sessions["max"], 1) if sessions["max"] else None
        )
        metrics: dict[str, float | int | None] = {
            "latency_ms": latency_ms,
            "connections.total": sessions["total"],
            "connections.active": sessions["active"],
            "connections.idle": sessions["idle"],
            "connections.max": sessions["max"],
            "connections.used_pct": used_pct,
            "db.size_bytes": base["size"],
            "uptime_s": base["uptime_s"],
            "queries.long_running": len(long_running),
            "locks.blocked": blocked,
            "cache.hit_ratio": round(db["hit"], 4) if db and db["hit"] is not None else None,
            "replication.lag_s": round(lag, 1) if lag is not None else None,
            "pg.xid_age_pct": round(100 * xid["pct"], 2)
            if xid and xid["pct"] is not None
            else None,
            # Cumulative counters; the health service turns them into *_delta / *_per_s.
            "deadlocks.total": db["deadlocks"] if db else None,
            "tx.commits.total": db["xact_commit"] if db else None,
            "tx.rollbacks.total": db["xact_rollback"] if db else None,
        }
        details = {
            "long_running": [
                {"pid": r["pid"], "user": r["usename"], "runtime_s": r["runtime_s"],
                 "query": mask_query(r["query"]) if base["monitor"] else None}
                for r in long_running
            ],
            "in_recovery": base["in_recovery"],
        }  # fmt: skip
        return StatusSnapshot(taken_at=now(), reachable=True, latency_ms=latency_ms,
                              server_version=short_version(base["version"]), uptime_s=base["uptime_s"],
                              db_size_bytes=base["size"], metrics=metrics, details=details,
                              unavailable=unavailable)  # fmt: skip

    # ------------------------------------------------------------------ analytics
    def list_tables(self, s):
        return pg_catalog.list_tables(s)

    def table_stats(self, s):
        return pg_catalog.table_stats(s)

    def freshness_max(self, s, schema, table, column):
        return pg_catalog.freshness_max(s, schema, table, column)

    def exact_count(self, s, schema, table):
        return pg_catalog.exact_count(s, schema, table)

    def sample(self, s, schema, table, columns, n, row_estimate):
        return pg_catalog.sample(s, schema, table, columns, n, row_estimate)

    def preview(self, s, schema, table, limit=50):
        return pg_catalog.preview(s, schema, table, limit)

    def run_readonly(self, s, statement, *, wrap, max_rows, max_bytes, timeout_s):
        return pg_catalog.run_readonly(s, statement, wrap=wrap, max_rows=max_rows, max_bytes=max_bytes,
                                       timeout_s=timeout_s)  # fmt: skip

    def explain(self, s, statement):
        return pg_catalog.explain(s, statement)

    # ---------------------------------------------------------------- grant script
    @staticmethod
    def readonly_grant_script(database: str, username: str) -> str:
        db = '"' + (database or "your_database").replace('"', '""') + '"'
        user = '"' + (username or "lens_ro").replace('"', '""') + '"'
        return f"""-- Read-only role for RideOps Lens (run as an admin on {db})
CREATE ROLE {user} LOGIN PASSWORD 'choose-a-strong-password' CONNECTION LIMIT 5;
GRANT CONNECT ON DATABASE {db} TO {user};
GRANT USAGE ON SCHEMA public TO {user};
GRANT SELECT ON ALL TABLES IN SCHEMA public TO {user};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {user};
GRANT pg_monitor TO {user};            -- optional: full status metrics
ALTER ROLE {user} SET default_transaction_read_only = on;
ALTER ROLE {user} SET statement_timeout = '30s';
-- Repeat the USAGE/SELECT/DEFAULT PRIVILEGES lines for every other schema Lens should see.
"""
