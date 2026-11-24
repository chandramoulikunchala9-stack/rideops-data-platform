"""Run guarded, read-only statements against a stored connection (FR-34, FR-36, §14.5)."""

import contextlib
import json
import logging
from typing import Any

import psycopg

from rideops_api.jobs.registry import JobContext, job
from rideops_api.lens import pools
from rideops_api.lens.catalog import detect
from rideops_api.lens.connections.service import TargetUnavailable, load_target
from rideops_api.lens.queries import sqlguard
from rideops_api.settings import get_lens_settings

logger = logging.getLogger(__name__)
DEFAULT_ROWS = 1_000
MAX_ROWS = 10_000
MAX_BYTES = 10_000_000
TIMEOUT_S = 30
CACHE_ROWS = 2_000  # dashboards cache at most this many rows per widget
MASK = "•••"


def _driver_errors() -> tuple[type[BaseException], ...]:
    import sqlite3

    errors: list[type[BaseException]] = [psycopg.Error, sqlite3.Error]
    for mod, attr in (
        ("pymysql", "MySQLError"),
        ("pymssql", "Error"),
        ("pymongo.errors", "PyMongoError"),
    ):
        with contextlib.suppress(ImportError):  # optional driver not installed
            errors.append(getattr(__import__(mod, fromlist=[attr]), attr))
    return tuple(errors)


DRIVER_ERRORS = _driver_errors()


def classify_query_error(exc: BaseException) -> tuple[int, str, str]:
    """Any driver's error → (HTTP status, catalogue code, first line of the message)."""
    message = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:500]
    low = message.lower()
    if (
        isinstance(exc, psycopg.errors.QueryCanceled)
        or type(exc).__name__ == "ExecutionTimeout"
        or any(
            k in low
            for k in (
                "statement timeout",
                "maximum statement execution time",
                "interrupted",
                "timeout expired",
            )
        )
    ):
        return 504, "QUERY_TIMEOUT", f"Query exceeded {TIMEOUT_S} s."
    if (
        isinstance(exc, psycopg.errors.ReadOnlySqlTransaction)
        or "read only" in low
        or "read-only" in low
    ):
        return 400, "QUERY_NOT_READONLY", message
    return 400, "QUERY_ERROR", message


def validate_for(engine: str, statement: str, *, demo: bool = False) -> sqlguard.Verdict:
    """SQL engines go through the sqlglot guard; MongoDB takes find/aggregate JSON."""
    if engine == "mongodb":
        return sqlguard.validate_mongo(statement, demo=demo)
    return sqlguard.validate(statement, engine, demo=demo)


class QueryFailed(Exception):
    def __init__(self, status: int, code: str, message: str, run_id: str | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.run_id = status, code, message, run_id


def pii_columns_for(conn, connection_id: Any, tables: list[str]) -> set[str]:
    """PII column names of the tables a statement reads (catalog), plus name heuristics on output."""
    if not tables:
        return set()
    names = [t.split(".")[-1] for t in tables]
    rows = conn.execute(
        "SELECT unnest(pii_columns) AS c FROM app.catalog_tables WHERE connection_id = %s AND table_name = ANY(%s)",
        (connection_id, names),
    ).fetchall()
    return {r["c"] for r in rows}


def _record(db, *, workspace_id, connection_id, statement, status, user_id, saved_query_id=None, result=None,
            error_code=None, error_message=None, cache=None, cache_ttl_s=None) -> str:  # fmt: skip
    with db.tenant_transaction(workspace_id) as conn:
        row = conn.execute(
            """INSERT INTO app.query_runs (workspace_id, connection_id, saved_query_id, statement_hash, statement,
                      status, row_count, truncated, elapsed_ms, error_code, error_message, result_cache,
                      cache_expires_at, run_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       CASE WHEN %s::int IS NULL THEN NULL ELSE now() + make_interval(secs => %s) END, %s)
               RETURNING id""",
            (workspace_id, connection_id, saved_query_id, sqlguard.statement_hash(statement), statement[:100_000],
             status, (result or {}).get("row_count"), (result or {}).get("truncated"), (result or {}).get("elapsed_ms"),
             error_code, (error_message or "")[:1000] or None, json.dumps(cache) if cache is not None else None,
             cache_ttl_s, cache_ttl_s, user_id),
        ).fetchone()  # fmt: skip
    return str(row["id"])


def run(db: Any, *, workspace_id: Any, connection_id: Any, statement: str, user_id: Any, settings=None,
        max_rows: int = DEFAULT_ROWS, reveal: bool = False, saved_query_id: Any = None,
        cache_ttl_s: int | None = None) -> dict[str, Any]:  # fmt: skip
    settings = settings or get_lens_settings()
    max_rows = max(1, min(int(max_rows), MAX_ROWS))
    with db.pool.connection() as conn:
        engine = conn.execute("SELECT engine::text AS e, is_demo FROM app.connections WHERE id = %s",
                              (connection_id,)).fetchone()  # fmt: skip
    verdict = validate_for(
        engine["e"] if engine else "postgres", statement, demo=bool(engine and engine["is_demo"])
    )
    if not verdict.ok:
        run_id = _record(db, workspace_id=workspace_id, connection_id=connection_id, statement=statement,
                         status="rejected", user_id=user_id, saved_query_id=saved_query_id,
                         error_code=verdict.code, error_message=verdict.message)  # fmt: skip
        raise QueryFailed(
            422, verdict.code or "QUERY_REJECTED", verdict.message or "Rejected.", run_id
        )
    exec_text = (
        verdict.statement
        if verdict.kind in ("select", "find", "aggregate")
        else statement.strip().rstrip(";")
    )
    try:
        _, adapter = load_target(db, connection_id, settings)
        with (
            pools.target_slot(str(connection_id), settings.max_target_sessions, wait_s=5.0),
            adapter.connect() as t,
        ):
            result = adapter.run_readonly(t, exec_text, wrap=verdict.wrap, max_rows=max_rows, max_bytes=MAX_BYTES,
                                             timeout_s=TIMEOUT_S)  # fmt: skip
    except TargetUnavailable as exc:
        raise QueryFailed(409, exc.code, exc.message) from None
    except pools.TargetBusy as exc:
        raise QueryFailed(429, "RATE_LIMITED", str(exc)) from None
    except DRIVER_ERRORS as exc:
        status, code, message = classify_query_error(exc)
        run_id = _record(db, workspace_id=workspace_id, connection_id=connection_id, statement=statement,
                         status="error", user_id=user_id, saved_query_id=saved_query_id, error_code=code,
                         error_message=message)  # fmt: skip
        raise QueryFailed(status, code, message, run_id) from None

    with db.pool.connection() as conn:
        pii = pii_columns_for(conn, connection_id, verdict.tables)
    names = [c["name"] for c in result["columns"]]
    masked = sorted({n for n in names if n in pii or detect.is_pii_name(n)})
    if masked and not reveal:
        idx = {i for i, n in enumerate(names) if n in masked}
        result["rows"] = [
            [MASK if i in idx and v is not None else v for i, v in enumerate(r)]
            for r in result["rows"]
        ]
    result["masked_columns"] = [] if reveal else masked
    cache = None
    if cache_ttl_s:
        cache = {**result, "rows": result["rows"][:CACHE_ROWS], "truncated": result["truncated"]
                 or len(result["rows"]) > CACHE_ROWS}  # fmt: skip
    result["run_id"] = _record(db, workspace_id=workspace_id, connection_id=connection_id, statement=statement,
                               status="ok", user_id=user_id, saved_query_id=saved_query_id, result=result,
                               cache=cache, cache_ttl_s=cache_ttl_s)  # fmt: skip
    result["kind"] = verdict.kind
    return result


def explain(db: Any, *, connection_id: Any, statement: str, settings=None) -> Any:
    settings = settings or get_lens_settings()
    with db.pool.connection() as conn:
        row = conn.execute(
            "SELECT is_demo, engine::text AS e FROM app.connections WHERE id = %s", (connection_id,)
        ).fetchone()
    verdict = validate_for(
        row["e"] if row else "postgres", statement, demo=bool(row and row["is_demo"])
    )
    if not verdict.ok or verdict.kind not in ("select", "find", "aggregate"):
        raise QueryFailed(
            422,
            verdict.code or "QUERY_NOT_READONLY",
            verdict.message or "Only SELECT can be explained.",
        )
    try:
        _, adapter = load_target(db, connection_id, settings)
        with (
            pools.target_slot(str(connection_id), settings.max_target_sessions, wait_s=5.0),
            adapter.connect() as t,
        ):
            return adapter.explain(t, verdict.statement)
    except TargetUnavailable as exc:
        raise QueryFailed(409, exc.code, exc.message) from None
    except DRIVER_ERRORS as exc:
        raise QueryFailed(
            400, "QUERY_ERROR", (str(exc).strip().splitlines() or ["failed"])[0][:500]
        ) from None


def refresh_dashboard(
    db: Any, dashboard_id: Any, *, user_id: Any = None, settings=None
) -> dict[str, int]:
    """Run every saved-query widget and cache its result (FR-35). Failures are recorded per run."""
    settings = settings or get_lens_settings()
    with db.pool.connection() as conn:
        dash = conn.execute(
            "SELECT * FROM app.dashboards WHERE id = %s", (dashboard_id,)
        ).fetchone()
        widgets = conn.execute(
            """SELECT w.*, q.statement, q.connection_id AS q_connection FROM app.widgets w
                 JOIN app.saved_queries q ON q.id = (w.source->>'id')::uuid
                WHERE w.dashboard_id = %s AND w.source->>'type' = 'saved_query'""",
            (dashboard_id,),
        ).fetchall()
    ttl = (dash or {}).get("refresh_interval_s") or 900
    ok = failed = 0
    for w in widgets:
        try:
            run(db, workspace_id=dash["workspace_id"], connection_id=w["q_connection"], statement=w["statement"],
                user_id=user_id, settings=settings, max_rows=CACHE_ROWS, saved_query_id=w["source"]["id"],
                cache_ttl_s=ttl)  # fmt: skip
            ok += 1
        except QueryFailed as exc:
            logger.info("dashboard %s widget %s failed: %s", dashboard_id, w["id"], exc.message)
            failed += 1
    with db.pool.connection() as conn:
        conn.execute(
            "UPDATE app.dashboards SET last_refreshed_at = now() WHERE id = %s", (dashboard_id,)
        )
    return {"ok": ok, "failed": failed}


@job("dashboards_due", timeout_s=300, max_attempts=1, priority=20)
def dashboards_due_job(ctx: JobContext) -> None:
    """Scheduled refresh: every dashboard whose interval has elapsed (min 15 min)."""
    with ctx.db.pool.connection() as conn:
        due = conn.execute(
            """SELECT id FROM app.dashboards WHERE refresh_interval_s IS NOT NULL
                  AND (last_refreshed_at IS NULL
                       OR last_refreshed_at + make_interval(secs => refresh_interval_s) <= now())
                LIMIT 50"""
        ).fetchall()
    for d in due:
        refresh_dashboard(ctx.db, d["id"], settings=ctx.settings)
