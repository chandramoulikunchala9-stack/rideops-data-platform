"""Query console, saved queries, dashboards and exports (§13.6)."""

import csv
import io
import json
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from rideops_api import audit
from rideops_api.auth.deps import LensSettingsDep, MetaDbDep
from rideops_api.errors import ApiError
from rideops_api.lens.queries import service, sqlguard
from rideops_api.workspaces.deps import WorkspaceContext, require

router = APIRouter(tags=["queries"])
Viewer = Annotated[WorkspaceContext, require("connection.view")]
Editor = Annotated[WorkspaceContext, require("query.run")]
DashEditor = Annotated[WorkspaceContext, require("dashboard.edit")]

QUERIES_PER_MINUTE = 30
CONN = "/workspaces/{ws}/connections/{connection_id}"


class StatementIn(BaseModel):
    statement: str = Field(min_length=1, max_length=100_000)


class RunIn(StatementIn):
    max_rows: int = Field(default=service.DEFAULT_ROWS, ge=1, le=service.MAX_ROWS)
    reveal: bool = False
    saved_query_id: UUID | None = None


class SavedQueryIn(BaseModel):
    connection_id: UUID
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    statement: str = Field(min_length=1, max_length=100_000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class SavedQueryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    statement: str | None = Field(default=None, min_length=1, max_length=100_000)
    tags: list[str] | None = Field(default=None, max_length=20)


class DashboardIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    refresh_interval_s: int | None = Field(default=None, ge=900, le=86_400)


class DashboardPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    refresh_interval_s: int | None = Field(default=None, ge=900, le=86_400)


class WidgetIn(BaseModel):
    kind: Literal["kpi", "line", "bar", "table", "text"]
    title: str = Field(min_length=1, max_length=120)
    saved_query_id: UUID | None = None  # required except for text widgets
    options: dict[str, Any] = Field(default_factory=dict)  # x, y, format, text
    position: int = 0


def _connection(conn, ws: UUID, connection_id: UUID) -> dict:
    row = conn.execute("SELECT * FROM app.connections WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL",
                       (connection_id, ws)).fetchone()  # fmt: skip
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Connection not found.")
    return row


def _failed(exc: service.QueryFailed) -> ApiError:
    return ApiError(
        exc.status, exc.code, exc.message, {"run_id": exc.run_id} if exc.run_id else None
    )


def _rate_limit(conn, ws: UUID) -> None:
    n = conn.execute("SELECT count(*) AS n FROM app.query_runs WHERE workspace_id = %s "
                     "AND created_at > now() - interval '1 minute'", (ws,)).fetchone()["n"]  # fmt: skip
    if n >= QUERIES_PER_MINUTE:
        raise ApiError(
            429,
            "RATE_LIMITED",
            f"At most {QUERIES_PER_MINUTE} queries per minute.",
            {"retry_after_s": 60},
        )


# ------------------------------------------------------------------- console
@router.post(CONN + "/queries/validate")
def validate(connection_id: UUID, body: StatementIn, ctx: Editor, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = _connection(conn, ctx.workspace_id, connection_id)
    v = sqlguard.validate(body.statement, row["engine"], demo=row["is_demo"])
    return {"ok": v.ok, "kind": v.kind, "code": v.code, "message": v.message, "tables": v.tables}


@router.post(CONN + "/queries/run")
def run_query(connection_id: UUID, body: RunIn, ctx: Editor, request: Request, db: MetaDbDep,
              settings: LensSettingsDep) -> dict:  # fmt: skip
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _connection(conn, ctx.workspace_id, connection_id)
        _rate_limit(conn, ctx.workspace_id)
    try:
        result = service.run(db, workspace_id=ctx.workspace_id, connection_id=connection_id, statement=body.statement,
                             user_id=ctx.user_id, settings=settings, max_rows=body.max_rows, reveal=body.reveal,
                             saved_query_id=body.saved_query_id)  # fmt: skip
    except service.QueryFailed as exc:
        raise _failed(exc) from None
    finally:
        with db.tenant_transaction(ctx.workspace_id) as conn:
            audit.record(conn, "query.run", request=request, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
                         target_type="connection", target_id=connection_id,
                         metadata={"statement_hash": sqlguard.statement_hash(body.statement),
                                   "reveal": body.reveal})  # fmt: skip
    if body.reveal:
        with db.tenant_transaction(ctx.workspace_id) as conn:
            audit.record(conn, "pii.reveal", request=request, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
                         target_type="query_run", target_id=result["run_id"])  # fmt: skip
    return result


@router.post(CONN + "/queries/explain")
def explain(
    connection_id: UUID, body: StatementIn, ctx: Editor, db: MetaDbDep, settings: LensSettingsDep
) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _connection(conn, ctx.workspace_id, connection_id)
    try:
        return {
            "plan": service.explain(
                db, connection_id=connection_id, statement=body.statement, settings=settings
            )
        }
    except service.QueryFailed as exc:
        raise _failed(exc) from None


def _run_row(conn, ws: UUID, run_id: UUID) -> dict:
    row = conn.execute(
        "SELECT * FROM app.query_runs WHERE id = %s AND workspace_id = %s", (run_id, ws)
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Query run not found.")
    return row


@router.get("/workspaces/{ws}/query-runs")
def list_runs(
    ctx: Editor, db: MetaDbDep, connection_id: UUID | None = None, limit: int = 50
) -> list[dict]:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        return conn.execute(
            """SELECT id, connection_id, saved_query_id, statement, status, row_count, truncated, elapsed_ms,
                      error_code, created_at FROM app.query_runs
                WHERE workspace_id = %s AND (%s::uuid IS NULL OR connection_id = %s)
                ORDER BY created_at DESC LIMIT %s""",
            (ctx.workspace_id, connection_id, connection_id, max(1, min(limit, 200))),
        ).fetchall()


@router.get("/workspaces/{ws}/query-runs/{run_id}")
def get_run(run_id: UUID, ctx: Editor, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = _run_row(conn, ctx.workspace_id, run_id)
    row.pop("result_cache", None)
    return row


@router.get("/workspaces/{ws}/query-runs/{run_id}/export.csv")
def export_csv(
    run_id: UUID, ctx: Editor, request: Request, db: MetaDbDep, settings: LensSettingsDep
) -> StreamingResponse:
    """FR-36: re-runs the stored statement with the same guard, caps (≤ 10 000 rows) and PII masking.
    Results are never stored, so there is nothing else to export."""
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = _run_row(conn, ctx.workspace_id, run_id)
        _connection(conn, ctx.workspace_id, row["connection_id"])
        _rate_limit(conn, ctx.workspace_id)
    try:
        result = service.run(db, workspace_id=ctx.workspace_id, connection_id=row["connection_id"],
                             statement=row["statement"], user_id=ctx.user_id, settings=settings,
                             max_rows=service.MAX_ROWS)  # fmt: skip
    except service.QueryFailed as exc:
        raise _failed(exc) from None
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([c["name"] for c in result["columns"]])
    for r in result["rows"]:
        writer.writerow(
            ["" if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else v) for v in r]
        )
    with db.tenant_transaction(ctx.workspace_id) as conn:
        audit.record(conn, "query.export", request=request, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
                     target_type="query_run", target_id=run_id, metadata={"rows": result["row_count"]})  # fmt: skip
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="query-{run_id}.csv"'})  # fmt: skip


# ------------------------------------------------------------- saved queries
def _check_statement(statement: str) -> None:
    v = sqlguard.validate(statement)
    if not v.ok:
        raise ApiError(422, v.code or "QUERY_REJECTED", v.message or "Rejected.")


@router.get("/workspaces/{ws}/saved-queries")
def list_saved(ctx: Viewer, db: MetaDbDep, connection_id: UUID | None = None) -> list[dict]:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        return conn.execute(
            """SELECT q.*, c.name AS connection_name
                 FROM app.saved_queries q JOIN app.connections c ON c.id = q.connection_id
                WHERE q.workspace_id = %s AND c.deleted_at IS NULL AND (%s::uuid IS NULL OR q.connection_id = %s)
                ORDER BY q.updated_at DESC""",
            (ctx.workspace_id, connection_id, connection_id),
        ).fetchall()


@router.post("/workspaces/{ws}/saved-queries", status_code=status.HTTP_201_CREATED)
def create_saved(body: SavedQueryIn, ctx: Editor, request: Request, db: MetaDbDep) -> dict:
    _check_statement(body.statement)
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _connection(conn, ctx.workspace_id, body.connection_id)
        row = conn.execute(
            """INSERT INTO app.saved_queries (workspace_id, connection_id, name, description, statement, tags,
                                             created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (ctx.workspace_id, body.connection_id, body.name.strip(), body.description, body.statement, body.tags,
             ctx.user_id),
        ).fetchone()  # fmt: skip
        audit.record(conn, "saved_query.create", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="saved_query", target_id=row["id"])  # fmt: skip
    return row


def _saved(conn, ws: UUID, query_id: UUID) -> dict:
    row = conn.execute(
        "SELECT * FROM app.saved_queries WHERE id = %s AND workspace_id = %s", (query_id, ws)
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Saved query not found.")
    return row


@router.get("/workspaces/{ws}/saved-queries/{query_id}")
def get_saved(query_id: UUID, ctx: Viewer, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        return _saved(conn, ctx.workspace_id, query_id)


@router.patch("/workspaces/{ws}/saved-queries/{query_id}")
def update_saved(query_id: UUID, body: SavedQueryPatch, ctx: Editor, db: MetaDbDep) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if "statement" in changes:
        _check_statement(changes["statement"])
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = _saved(conn, ctx.workspace_id, query_id)
        if changes:
            sets = ", ".join(f"{k} = %({k})s" for k in changes)
            row = conn.execute(f"UPDATE app.saved_queries SET {sets}, updated_at = now() WHERE id = %(id)s RETURNING *",
                               {**changes, "id": query_id}).fetchone()  # fmt: skip
    return row


@router.delete("/workspaces/{ws}/saved-queries/{query_id}")
def delete_saved(query_id: UUID, ctx: Editor, request: Request, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _saved(conn, ctx.workspace_id, query_id)
        conn.execute("DELETE FROM app.widgets WHERE source->>'id' = %s", (str(query_id),))
        conn.execute("DELETE FROM app.saved_queries WHERE id = %s", (query_id,))
        audit.record(conn, "saved_query.delete", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="saved_query", target_id=query_id)  # fmt: skip
    return {"deleted": True}


# ---------------------------------------------------------------- dashboards
def _dashboard(conn, ws: UUID, dashboard_id: UUID) -> dict:
    row = conn.execute(
        "SELECT * FROM app.dashboards WHERE id = %s AND workspace_id = %s", (dashboard_id, ws)
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Dashboard not found.")
    return row


def _with_widgets(conn, dash: dict) -> dict:
    widgets = conn.execute(
        """SELECT w.*, r.result_cache, r.created_at AS refreshed_at, r.cache_expires_at, r.status AS last_status,
                  r.error_code AS last_error, q.name AS query_name
             FROM app.widgets w
             LEFT JOIN app.saved_queries q ON q.id = (w.source->>'id')::uuid
             LEFT JOIN LATERAL (SELECT * FROM app.query_runs x WHERE x.saved_query_id = q.id
                                  AND (x.result_cache IS NOT NULL OR x.status <> 'ok')
                                ORDER BY x.created_at DESC LIMIT 1) r ON true
            WHERE w.dashboard_id = %s ORDER BY w.position, w.title""",
        (dash["id"],),
    ).fetchall()
    now = datetime.now(timezone.utc)
    for w in widgets:
        w["stale"] = bool(w["cache_expires_at"]) and w["cache_expires_at"] < now
    return {**dash, "widgets": widgets}


@router.get("/workspaces/{ws}/dashboards")
def list_dashboards(ctx: Viewer, db: MetaDbDep) -> list[dict]:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        return conn.execute(
            """SELECT d.*, (SELECT count(*) FROM app.widgets w WHERE w.dashboard_id = d.id) AS widget_count
                 FROM app.dashboards d WHERE d.workspace_id = %s ORDER BY d.updated_at DESC""",
            (ctx.workspace_id,),
        ).fetchall()


@router.post("/workspaces/{ws}/dashboards", status_code=status.HTTP_201_CREATED)
def create_dashboard(body: DashboardIn, ctx: DashEditor, request: Request, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = conn.execute(
            """INSERT INTO app.dashboards (workspace_id, name, description, refresh_interval_s, created_by)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (
                ctx.workspace_id,
                body.name.strip(),
                body.description,
                body.refresh_interval_s,
                ctx.user_id,
            ),
        ).fetchone()
        audit.record(conn, "dashboard.create", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="dashboard", target_id=row["id"])  # fmt: skip
        return _with_widgets(conn, row)


@router.get("/workspaces/{ws}/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: UUID, ctx: Viewer, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        return _with_widgets(conn, _dashboard(conn, ctx.workspace_id, dashboard_id))


@router.patch("/workspaces/{ws}/dashboards/{dashboard_id}")
def update_dashboard(
    dashboard_id: UUID, body: DashboardPatch, ctx: DashEditor, db: MetaDbDep
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = _dashboard(conn, ctx.workspace_id, dashboard_id)
        if changes:
            sets = ", ".join(f"{k} = %({k})s" for k in changes)
            row = conn.execute(f"UPDATE app.dashboards SET {sets}, updated_at = now() WHERE id = %(id)s RETURNING *",
                               {**changes, "id": dashboard_id}).fetchone()  # fmt: skip
        return _with_widgets(conn, row)


@router.delete("/workspaces/{ws}/dashboards/{dashboard_id}")
def delete_dashboard(dashboard_id: UUID, ctx: DashEditor, request: Request, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _dashboard(conn, ctx.workspace_id, dashboard_id)
        conn.execute("DELETE FROM app.dashboards WHERE id = %s", (dashboard_id,))
        audit.record(conn, "dashboard.delete", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="dashboard", target_id=dashboard_id)  # fmt: skip
    return {"deleted": True}


@router.post(
    "/workspaces/{ws}/dashboards/{dashboard_id}/widgets", status_code=status.HTTP_201_CREATED
)
def add_widget(dashboard_id: UUID, body: WidgetIn, ctx: DashEditor, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _dashboard(conn, ctx.workspace_id, dashboard_id)
        if body.kind != "text":
            if body.saved_query_id is None:
                raise ApiError(
                    422, "validation_error", "Chart and table widgets need a saved query."
                )
            _saved(conn, ctx.workspace_id, body.saved_query_id)
        source = (
            {"type": "saved_query", "id": str(body.saved_query_id)}
            if body.saved_query_id
            else {"type": "text"}
        )
        row = conn.execute(
            """INSERT INTO app.widgets (dashboard_id, position, kind, title, source, options)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (
                dashboard_id,
                body.position,
                body.kind,
                body.title.strip(),
                json.dumps(source),
                json.dumps(body.options),
            ),
        ).fetchone()
        conn.execute("UPDATE app.dashboards SET updated_at = now() WHERE id = %s", (dashboard_id,))
    return row


@router.delete("/workspaces/{ws}/dashboards/{dashboard_id}/widgets/{widget_id}")
def delete_widget(dashboard_id: UUID, widget_id: UUID, ctx: DashEditor, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _dashboard(conn, ctx.workspace_id, dashboard_id)
        conn.execute(
            "DELETE FROM app.widgets WHERE id = %s AND dashboard_id = %s", (widget_id, dashboard_id)
        )
    return {"deleted": True}


@router.post("/workspaces/{ws}/dashboards/{dashboard_id}/refresh")
def refresh_dashboard(
    dashboard_id: UUID, ctx: DashEditor, db: MetaDbDep, settings: LensSettingsDep
) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _dashboard(conn, ctx.workspace_id, dashboard_id)
    counts = service.refresh_dashboard(db, dashboard_id, user_id=ctx.user_id, settings=settings)
    with db.tenant_transaction(ctx.workspace_id) as conn:
        return {
            **_with_widgets(conn, _dashboard(conn, ctx.workspace_id, dashboard_id)),
            "refresh": counts,
        }


@router.get(CONN + "/dashboard")
def auto_dashboard(connection_id: UUID, ctx: Viewer, db: MetaDbDep) -> dict:
    """F-AN-15 auto dashboard: built from Lens's own metadata (status, catalog, freshness, insights);
    it never queries the target database, so it is always cheap to open."""
    with db.tenant_transaction(ctx.workspace_id) as conn:
        c = _connection(conn, ctx.workspace_id, connection_id)
        latency = conn.execute(
            """SELECT date_trunc('hour', taken_at) AS at, avg(latency_ms)::int AS latency_ms,
                      round(100.0 * count(*) FILTER (WHERE state IN ('healthy', 'degraded')) / count(*), 2)
                        AS uptime_pct
                 FROM app.status_snapshots WHERE connection_id = %s AND taken_at > now() - interval '7 days'
                GROUP BY 1 ORDER BY 1""",
            (connection_id,),
        ).fetchall()
        largest = conn.execute(
            """SELECT DISTINCT ON (schema_name, table_name) schema_name, table_name, row_estimate, total_bytes
                 FROM app.table_stats_snapshots WHERE connection_id = %s
                ORDER BY schema_name, table_name, taken_at DESC""",
            (connection_id,),
        ).fetchall()
        freshness = conn.execute(
            """SELECT t.schema_name, t.table_name, t.freshness_column, s.freshness_max,
                      extract(epoch FROM now() - s.freshness_max)::bigint AS lag_s
                 FROM app.catalog_tables t
                 JOIN LATERAL (SELECT freshness_max FROM app.table_stats_snapshots x
                                WHERE x.connection_id = t.connection_id
                                  AND x.schema_name = t.schema_name AND x.table_name = t.table_name
                                ORDER BY taken_at DESC LIMIT 1) s ON true
                WHERE t.connection_id = %s AND t.dropped_at IS NULL AND t.freshness_column IS NOT NULL
                ORDER BY lag_s DESC NULLS LAST LIMIT 20""",
            (connection_id,),
        ).fetchall()
        insights = conn.execute(
            """SELECT severity, count(*) AS n FROM app.insights WHERE connection_id = %s AND resolved_at IS NULL
                  AND dismissed_at IS NULL GROUP BY 1""",
            (connection_id,),
        ).fetchall()
        changes = conn.execute(
            "SELECT count(*) AS n FROM app.schema_changes "
            "WHERE connection_id = %s AND detected_at > now() - interval '7 days'",
            (connection_id,),
        ).fetchone()["n"]
    largest = sorted(largest, key=lambda r: -(r["total_bytes"] or 0))[:10]
    return {
        "connection": {"id": c["id"], "name": c["name"], "current_state": c["current_state"],
                       "health_score": c["health_score"]},
        "latency_7d": latency,
        "largest_tables": largest,
        "total_bytes": sum(r["total_bytes"] or 0 for r in largest),
        "freshness": freshness,
        "insights": {r["severity"]: r["n"] for r in insights},
        "schema_changes_7d": changes,
    }  # fmt: skip
