"""Catalog, table analytics, profiles, preview, schema changes and insights (§13.5)."""

import json
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from rideops_api import audit
from rideops_api.auth.deps import LensSettingsDep, MetaDbDep
from rideops_api.errors import ApiError
from rideops_api.jobs import queue
from rideops_api.lens import pools
from rideops_api.lens.adapters import pg_catalog
from rideops_api.lens.connections.service import TargetUnavailable, load_target
from rideops_api.lens.profiling.service import PROFILE_DEFAULT_ROWS, PROFILE_MAX_ROWS
from rideops_api.workspaces.deps import WorkspaceContext, require

router = APIRouter(tags=["analytics"])
Viewer = Annotated[WorkspaceContext, require("connection.view")]
Editor = Annotated[WorkspaceContext, require("query.run")]
RevealPII = Annotated[WorkspaceContext, require("pii.reveal")]

EXACT_COUNT_MAX_ROWS = 5_000_000  # FR-31
PROFILES_PER_HOUR = 10
MASK = "•••"
BASE = "/workspaces/{ws}/connections/{connection_id}"


def _connection(conn, ws: UUID, connection_id: UUID) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM app.connections WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL",
        (connection_id, ws),
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Connection not found.")
    return row


def _table(conn, connection_id: UUID, schema: str, table: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT * FROM app.catalog_tables
            WHERE connection_id = %s AND schema_name = %s AND table_name = %s AND dropped_at IS NULL""",
        (connection_id, schema, table),
    ).fetchone()
    if row is None:
        raise ApiError(
            404, "NOT_FOUND", "Table not found. Sync the schema if it was created recently."
        )
    return row


def _unavailable(exc: TargetUnavailable) -> ApiError:
    return ApiError(404 if exc.code == "NOT_FOUND" else 409, exc.code, exc.message)


def mask_rows(columns: list[str], rows: list[list[Any]], pii: set[str]) -> list[list[Any]]:
    idx = [i for i, c in enumerate(columns) if c in pii]
    if not idx:
        return rows
    return [[MASK if i in idx and v is not None else v for i, v in enumerate(r)] for r in rows]


@router.post(BASE + "/catalog/sync", status_code=status.HTTP_202_ACCEPTED)
def catalog_sync(connection_id: UUID, ctx: Annotated[WorkspaceContext, require("connection.edit")],
                 request: Request, db: MetaDbDep) -> dict:  # fmt: skip
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = _connection(conn, ctx.workspace_id, connection_id)
        if row["is_paused"]:
            raise ApiError(409, "CONNECTION_PAUSED", "Resume the connection to sync it.")
        bucket = int(datetime.now(timezone.utc).timestamp()) // 60
        job_id = queue.enqueue(conn, "schema_sync", connection_id=connection_id, workspace_id=ctx.workspace_id,
                               idempotency_key=f"schema_sync:{connection_id}:manual:{bucket}")  # fmt: skip
        if job_id is None:
            raise ApiError(
                429,
                "RATE_LIMITED",
                "A sync was requested this minute; retry shortly.",
                {"retry_after_s": 60},
            )
        audit.record(conn, "catalog.sync", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="connection", target_id=connection_id)  # fmt: skip
    return {"job_id": job_id}


@router.get(BASE + "/catalog")
def catalog(connection_id: UUID, ctx: Viewer, db: MetaDbDep, search: str | None = None, schema: str | None = None,
            kind: str | None = None) -> dict:  # fmt: skip
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _connection(conn, ctx.workspace_id, connection_id)
        last = conn.execute(
            "SELECT * FROM app.catalog_syncs WHERE connection_id = %s ORDER BY started_at DESC LIMIT 1",
            (connection_id,),
        ).fetchone()
        rows = conn.execute(
            """SELECT t.schema_name, t.table_name, t.kind, t.comment, t.primary_key, t.freshness_column,
                      t.pii_columns, t.is_pinned, jsonb_array_length(t.columns) AS column_count,
                      (SELECT jsonb_agg(c->>'name') FROM jsonb_array_elements(t.columns) c) AS column_names,
                      s.row_estimate, s.total_bytes, s.freshness_max
                 FROM app.catalog_tables t
                 LEFT JOIN LATERAL (SELECT row_estimate, total_bytes, freshness_max FROM app.table_stats_snapshots x
                                     WHERE x.connection_id = t.connection_id AND x.schema_name = t.schema_name
                                       AND x.table_name = t.table_name ORDER BY taken_at DESC LIMIT 1) s ON true
                WHERE t.connection_id = %(c)s AND t.dropped_at IS NULL
                  AND (%(schema)s::text IS NULL OR t.schema_name = %(schema)s)
                  AND (%(kind)s::text IS NULL OR t.kind = %(kind)s)
                  AND (%(q)s::text IS NULL OR t.table_name ILIKE '%%' || %(q)s || '%%'
                       OR t.schema_name ILIKE '%%' || %(q)s || '%%'
                       OR EXISTS (SELECT 1 FROM jsonb_array_elements(t.columns) c
                                   WHERE c->>'name' ILIKE '%%' || %(q)s || '%%'))
                ORDER BY t.is_pinned DESC, t.schema_name, t.table_name""",
            {"c": connection_id, "schema": schema, "kind": kind, "q": search.strip() if search else None},
        ).fetchall()  # fmt: skip
    term = (search or "").strip().lower()
    tables = []
    for r in rows:
        matches = [c for c in (r.pop("column_names") or []) if term and term in c.lower()]
        tables.append({**r, "matching_columns": matches[:10]})
    schemas = sorted({t["schema_name"] for t in tables})
    return {"last_sync": last, "schemas": schemas, "tables": tables,
            "total_bytes": sum(t["total_bytes"] or 0 for t in tables)}  # fmt: skip


@router.get(BASE + "/tables/{schema}/{table}")
def table_detail(connection_id: UUID, schema: str, table: str, ctx: Viewer, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _connection(conn, ctx.workspace_id, connection_id)
        t = _table(conn, connection_id, schema, table)
        stats = conn.execute(
            """SELECT * FROM app.table_stats_snapshots WHERE connection_id = %s AND schema_name = %s AND table_name = %s
                ORDER BY taken_at DESC LIMIT 1""",
            (connection_id, schema, table),
        ).fetchone()
        changes = conn.execute(
            """SELECT detected_at, change_type, object_ref, before, after FROM app.schema_changes
                WHERE connection_id = %s AND (object_ref = %s OR object_ref LIKE %s OR object_ref LIKE %s)
                ORDER BY detected_at DESC LIMIT 50""",
            (connection_id, f"{schema}.{table}", f"{schema}.{table}.%", f"{schema}.{table}#%"),
        ).fetchall()
        referenced_by = conn.execute(
            """SELECT schema_name, table_name, fk FROM app.catalog_tables, jsonb_array_elements(foreign_keys) fk
                WHERE connection_id = %s AND dropped_at IS NULL AND fk->>'ref_table' = %s""",
            (connection_id, f"{schema}.{table}"),
        ).fetchall()
    for col in t["columns"]:
        col["pii"] = col["name"] in t["pii_columns"]
    return {"table": t, "stats": stats, "changes": changes, "referenced_by": referenced_by}


@router.get(BASE + "/tables/{schema}/{table}/stats/history")
def stats_history(connection_id: UUID, schema: str, table: str, ctx: Viewer, db: MetaDbDep,
                  days: Annotated[int, Query(ge=1, le=365)] = 30) -> list[dict]:  # fmt: skip
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _connection(conn, ctx.workspace_id, connection_id)
        return conn.execute(
            """SELECT taken_at, row_estimate, row_exact, total_bytes, index_bytes, dead_rows, freshness_max
                 FROM app.table_stats_snapshots
                WHERE connection_id = %s AND schema_name = %s AND table_name = %s
                  AND taken_at > now() - make_interval(days => %s)
                ORDER BY taken_at""",
            (connection_id, schema, table, days),
        ).fetchall()


@router.post(BASE + "/tables/{schema}/{table}/count")
def exact_count(connection_id: UUID, schema: str, table: str, ctx: Editor, request: Request, db: MetaDbDep,
                settings: LensSettingsDep) -> dict:  # fmt: skip
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _connection(conn, ctx.workspace_id, connection_id)
        _table(conn, connection_id, schema, table)
        est = conn.execute(
            """SELECT row_estimate FROM app.table_stats_snapshots WHERE connection_id = %s AND schema_name = %s
                  AND table_name = %s ORDER BY taken_at DESC LIMIT 1""",
            (connection_id, schema, table),
        ).fetchone()
    if est and (est["row_estimate"] or 0) > EXACT_COUNT_MAX_ROWS:
        raise ApiError(409, "PROFILE_TOO_EXPENSIVE", f"About {est['row_estimate']:,} rows: exact counts are limited to "
                       f"tables under {EXACT_COUNT_MAX_ROWS:,} rows. The estimate is shown instead.")  # fmt: skip
    try:
        _, adapter = load_target(db, connection_id, settings)
        with (
            pools.target_slot(str(connection_id), settings.max_target_sessions),
            adapter.connect() as t,
        ):
            n = pg_catalog.exact_count(t, schema, table)
    except TargetUnavailable as exc:
        raise _unavailable(exc) from None
    with db.tenant_transaction(ctx.workspace_id) as conn:
        conn.execute(
            """UPDATE app.table_stats_snapshots SET row_exact = %s
                WHERE (connection_id, schema_name, table_name, taken_at) = (SELECT connection_id, schema_name,
                       table_name, max(taken_at) FROM app.table_stats_snapshots WHERE connection_id = %s
                       AND schema_name = %s AND table_name = %s GROUP BY 1, 2, 3)""",
            (n, connection_id, schema, table),
        )
        audit.record(conn, "table.count", request=request, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
                     target_type="table", target_id=f"{connection_id}:{schema}.{table}")  # fmt: skip
    return {"row_exact": n}


@router.post(BASE + "/tables/{schema}/{table}/profile", status_code=status.HTTP_202_ACCEPTED)
def request_profile(
    connection_id: UUID,
    schema: str,
    table: str,
    ctx: Editor,
    request: Request,
    db: MetaDbDep,
    sample_rows: Annotated[int, Query(ge=100, le=PROFILE_MAX_ROWS)] = PROFILE_DEFAULT_ROWS,
) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = _connection(conn, ctx.workspace_id, connection_id)
        if row["is_paused"]:
            raise ApiError(409, "CONNECTION_PAUSED", "Resume the connection to profile it.")
        _table(conn, connection_id, schema, table)
        recent = conn.execute(
            """SELECT count(*) AS n FROM app.jobs WHERE connection_id = %s AND job_type = 'profile_table'
                  AND created_at > now() - interval '1 hour'""",
            (connection_id,),
        ).fetchone()["n"]
        if recent >= PROFILES_PER_HOUR:
            raise ApiError(429, "RATE_LIMITED", f"Profiling is limited to {PROFILES_PER_HOUR} runs per hour "
                           "per connection.",
                           {"retry_after_s": 3600})  # fmt: skip
        job_id = queue.enqueue(
            conn,
            "profile_table",
            connection_id=connection_id,
            workspace_id=ctx.workspace_id,
            payload={"schema": schema, "table": table, "sample_rows": sample_rows},
            priority=90,
        )
        audit.record(conn, "table.profile", request=request, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
                     target_type="table", target_id=f"{connection_id}:{schema}.{table}")  # fmt: skip
    return {"job_id": job_id}


@router.get(BASE + "/tables/{schema}/{table}/profile")
def latest_profile(
    connection_id: UUID, schema: str, table: str, ctx: Viewer, db: MetaDbDep
) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _connection(conn, ctx.workspace_id, connection_id)
        prof = conn.execute(
            """SELECT profiled_at, sample_rows, method, elapsed_ms, profiles, charts FROM app.column_profiles
                WHERE connection_id = %s AND schema_name = %s AND table_name = %s ORDER BY profiled_at DESC LIMIT 1""",
            (connection_id, schema, table),
        ).fetchone()
        pending = conn.execute(
            """SELECT 1 FROM app.jobs WHERE connection_id = %s AND job_type = 'profile_table'
                  AND status IN ('queued', 'running') AND payload->>'schema' = %s AND payload->>'table' = %s""",
            (connection_id, schema, table),
        ).fetchone()
    return {"profile": prof, "pending": bool(pending)}


@router.get(BASE + "/tables/{schema}/{table}/profile/export.json")
def export_profile(
    connection_id: UUID, schema: str, table: str, ctx: Viewer, db: MetaDbDep
) -> JSONResponse:
    body = latest_profile(connection_id, schema, table, ctx, db)
    if not body["profile"]:
        raise ApiError(404, "NOT_FOUND", "No profile yet.")
    filename = f"{schema}.{table}.profile.json".replace('"', "")
    return JSONResponse(content=json.loads(json.dumps(body["profile"], default=str)),
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})  # fmt: skip


@router.get(BASE + "/tables/{schema}/{table}/preview")
def preview(connection_id: UUID, schema: str, table: str, ctx: Editor, request: Request, db: MetaDbDep,
            settings: LensSettingsDep, reveal: bool = False) -> dict:  # fmt: skip
    """F-AN-11: 50 rows, PII columns masked unless the caller explicitly reveals (audited, FR-33)."""
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _connection(conn, ctx.workspace_id, connection_id)
        t = _table(conn, connection_id, schema, table)
    try:
        _, adapter = load_target(db, connection_id, settings)
        with (
            pools.target_slot(str(connection_id), settings.max_target_sessions),
            adapter.connect() as tc,
        ):
            columns, rows = pg_catalog.preview(tc, schema, table, 50)
    except TargetUnavailable as exc:
        raise _unavailable(exc) from None
    pii = set(t["pii_columns"])
    if reveal and pii:
        with db.tenant_transaction(ctx.workspace_id) as conn:
            audit.record(conn, "pii.reveal", request=request, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
                         target_type="table", target_id=f"{connection_id}:{schema}.{table}",
                         metadata={"columns": sorted(pii)})  # fmt: skip
    else:
        rows = mask_rows(columns, rows, pii)
    return {
        "columns": columns,
        "rows": rows,
        "masked_columns": [] if reveal else sorted(pii & set(columns)),
    }


@router.post(BASE + "/tables/{schema}/{table}/pin")
def pin(
    connection_id: UUID, schema: str, table: str, ctx: Editor, db: MetaDbDep, pinned: bool = True
) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _connection(conn, ctx.workspace_id, connection_id)
        _table(conn, connection_id, schema, table)
        conn.execute(
            "UPDATE app.catalog_tables SET is_pinned = %s "
            "WHERE connection_id = %s AND schema_name = %s AND table_name = %s",
            (pinned, connection_id, schema, table),
        )
    return {"pinned": pinned}


@router.get(BASE + "/schema-changes")
def schema_changes(connection_id: UUID, ctx: Viewer, db: MetaDbDep,
                   from_: Annotated[datetime | None, Query(alias="from")] = None,
                   limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict]:  # fmt: skip
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _connection(conn, ctx.workspace_id, connection_id)
        return conn.execute(
            """SELECT id, detected_at, change_type, object_ref, before, after FROM app.schema_changes
                WHERE connection_id = %s AND (%s::timestamptz IS NULL OR detected_at >= %s)
                ORDER BY detected_at DESC, id DESC LIMIT %s""",
            (connection_id, from_, from_, limit),
        ).fetchall()


@router.get(BASE + "/insights")
def insights(
    connection_id: UUID, ctx: Viewer, db: MetaDbDep, include_dismissed: bool = False
) -> list[dict]:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _connection(conn, ctx.workspace_id, connection_id)
        return conn.execute(
            """SELECT id, code, severity, object_ref, title, detail, recommendation, first_seen_at, last_seen_at,
                      dismissed_at FROM app.insights
                WHERE connection_id = %s AND resolved_at IS NULL AND (%s OR dismissed_at IS NULL)
                ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, last_seen_at DESC""",
            (connection_id, include_dismissed),
        ).fetchall()


@router.post("/workspaces/{ws}/insights/{insight_id}/dismiss")
def dismiss_insight(insight_id: int, ctx: Annotated[WorkspaceContext, require("connection.edit")], request: Request,
                    db: MetaDbDep) -> dict:  # fmt: skip
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = conn.execute(
            """UPDATE app.insights i SET dismissed_at = now(), dismissed_by = %s
                FROM app.connections c WHERE i.id = %s AND c.id = i.connection_id AND c.workspace_id = %s
            RETURNING i.id""",
            (ctx.user_id, insight_id, ctx.workspace_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "Insight not found.")
        audit.record(conn, "insight.dismiss", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="insight", target_id=insight_id)  # fmt: skip
    return {"dismissed": True}
