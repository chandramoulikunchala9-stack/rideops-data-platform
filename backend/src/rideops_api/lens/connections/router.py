"""/workspaces/{ws}/connections* and /status* (§13.3, §13.4). Tenant queries run inside
`tenant_transaction`, so Row-Level Security backs up every WHERE workspace_id filter."""

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from rideops_api import audit
from rideops_api.auth.deps import LensSettingsDep, MetaDbDep
from rideops_api.errors import ApiError
from rideops_api.jobs import queue
from rideops_api.lens.adapters.registry import (
    AdapterUnavailable,
    available_engines,
    get_adapter_class,
)
from rideops_api.lens.connections import schemas as s
from rideops_api.lens.connections import service, url_parser
from rideops_api.lens.security.crypto import DecryptError, KeyRingError
from rideops_api.lens.status.health import uptime
from rideops_api.workspaces.deps import WorkspaceContext, require

router = APIRouter(tags=["connections"])
meta_router = APIRouter(tags=["system"])

Viewer = Annotated[WorkspaceContext, require("connection.view")]
Editor = Annotated[WorkspaceContext, require("connection.edit")]
Admin = Annotated[WorkspaceContext, require("connection.rotate_secret")]

TESTS_PER_MINUTE = 10
RESTORE_WINDOW_DAYS = 7
CHECK_NOW_BUCKET_S = 10


@contextmanager
def _errors():
    """Translate domain errors into catalogue codes (§22)."""
    try:
        yield
    except url_parser.UrlError as exc:
        raise ApiError(422, exc.code, exc.message, exc.details) from None
    except AdapterUnavailable as exc:
        raise ApiError(
            500 if exc.code == "DRIVER_MISSING" else 422, exc.code, exc.message
        ) from None
    except KeyRingError as exc:
        raise ApiError(500, "LENS_KEYS_MISSING", "Server encryption keys are not configured (ops issue).",
                       {"hint": str(exc)}) from None  # fmt: skip
    except DecryptError:
        raise ApiError(
            500, "SECRET_UNAVAILABLE", "Stored credentials could not be decrypted (ops issue)."
        ) from None


def _conn_out(row: dict, sparkline: list | None = None) -> s.ConnectionOut:
    fields = {k: row.get(k) for k in s.ConnectionOut.model_fields if k in row}
    return s.ConnectionOut(**fields, sparkline=sparkline or [])


def _get(conn, ws, connection_id, *, include_deleted: bool = False) -> dict:
    row = conn.execute(
        f"""SELECT c.*, EXISTS (SELECT 1 FROM app.connection_secrets x WHERE x.connection_id = c.id) AS has_secret
              FROM app.connections c WHERE c.id = %s AND c.workspace_id = %s
              {"" if include_deleted else "AND c.deleted_at IS NULL"}""",
        (connection_id, ws),
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Connection not found.")
    return row


def _test_out(
    test_id: str, result, engine: str, username: str | None, database: str | None
) -> s.TestOut:
    needs_script = any(f.get("severity") == "warning" for f in result.privilege_findings)
    script = (
        get_adapter_class(engine).readonly_grant_script(database or "", username or "lens_ro")
        if needs_script
        else None
    )
    return s.TestOut(test_id=test_id, ok=result.ok, server_version=result.server_version,
                     stages=[s.StageOut(**st.as_dict()) for st in result.stages],
                     capabilities=sorted(result.capabilities), privilege_findings=result.privilege_findings,
                     grant_script=script)  # fmt: skip


def _check_test_rate(conn, ws) -> None:
    n = conn.execute(
        "SELECT count(*) AS n FROM app.connection_tests "
        "WHERE workspace_id = %s AND created_at > now() - interval '1 minute'",
        (ws,),
    ).fetchone()["n"]
    if n >= TESTS_PER_MINUTE:
        raise ApiError(
            429,
            "RATE_LIMITED",
            "Too many connection tests; retry in a minute.",
            {"retry_after_s": 60},
        )


def _verified_test(conn, ctx: WorkspaceContext, test_id: UUID, draft: service.Draft, settings, *,
                   connection_id: UUID | None, force_reason: str | None, request: Request) -> dict:  # fmt: skip
    """FR-13: a save needs a passing test of *this exact* config + secret, at most N minutes old."""
    row = conn.execute(
        """SELECT * FROM app.connection_tests
            WHERE id = %s AND workspace_id = %s AND (connection_id IS NULL OR connection_id = %s)
              AND created_at > now() - make_interval(mins => %s)""",
        (test_id, ctx.workspace_id, connection_id, settings.connection_test_max_age_min),
    ).fetchone()
    keyring = service.keyring_for(settings)
    if row is None or row["config_fingerprint"] != keyring.fingerprint(draft.fingerprint_data()):
        raise ApiError(409, "TEST_REQUIRED", "Test this exact configuration (within the last "
                       f"{settings.connection_test_max_age_min} minutes) before saving.")  # fmt: skip
    if not row["ok"]:
        if not (force_reason and ctx.can("connection.rotate_secret")):
            raise ApiError(
                409,
                "TEST_FAILED",
                "The connection test failed. Fix it, or ask an admin to force-save.",
            )
        audit.record(conn, "connection.force_save", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="connection_test", target_id=test_id,
                     metadata={"reason": force_reason})  # fmt: skip
    return row


# --------------------------------------------------------------------------- meta
@meta_router.get("/meta/engines")
def engines(settings: LensSettingsDep) -> dict:
    return {"engines": available_engines(), "tls_modes": list(url_parser.TLS_MODES),
            "egress_ips": [ip.strip() for ip in settings.egress_ips.split(",") if ip.strip()]}  # fmt: skip


# ------------------------------------------------------------------- parse & test
@router.post("/workspaces/{ws}/connections/parse-url", response_model=s.ParsedOut)
def parse_url(body: s.ParseUrlIn, ctx: Editor) -> s.ParsedOut:
    with _errors():
        return s.ParsedOut(**url_parser.parse(body.url).public_fields())


@router.post("/workspaces/{ws}/connections/test", response_model=s.TestOut)
def test_draft(
    body: s.DraftIn, ctx: Editor, request: Request, db: MetaDbDep, settings: LensSettingsDep
) -> s.TestOut:
    with _errors():
        draft = service.resolve_draft(
            body.model_dump(exclude={"secret"}), body.secret.model_dump(exclude_none=True)
        )
        keyring = service.keyring_for(settings)
        with db.tenant_transaction(ctx.workspace_id) as conn:
            _check_test_rate(conn, ctx.workspace_id)
        result = service.run_test(
            draft, workspace_settings=ctx.workspace["settings"], settings=settings
        )
        with db.tenant_transaction(ctx.workspace_id) as conn:
            test_id = service.record_test(conn, workspace_id=ctx.workspace_id, connection_id=None,
                                          user_id=ctx.user_id, result=result,
                                          fingerprint=keyring.fingerprint(draft.fingerprint_data()))  # fmt: skip
            failure = result.failure
            if failure and failure.stage == "NETWORK_POLICY":
                audit.record(conn, "network.blocked", request=request, workspace_id=ctx.workspace_id,
                             actor_user_id=ctx.user_id, metadata={"host": draft.host, "port": draft.port,
                                                                   "code": failure.code})  # fmt: skip
        return _test_out(test_id, result, draft.engine, draft.username, draft.database)


# --------------------------------------------------------------------------- CRUD
@router.post(
    "/workspaces/{ws}/connections",
    response_model=s.ConnectionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_connection(body: s.ConnectionCreate, ctx: Editor, request: Request, db: MetaDbDep,
                      settings: LensSettingsDep) -> s.ConnectionOut:  # fmt: skip
    if not ctx.session.email_verified:
        raise ApiError(403, "EMAIL_NOT_VERIFIED", "Verify your email before adding connections.")
    with _errors():
        draft = service.resolve_draft(body.draft.model_dump(exclude={"secret"}),
                                      body.draft.secret.model_dump(exclude_none=True))  # fmt: skip
        with db.tenant_transaction(ctx.workspace_id) as conn:
            test = _verified_test(conn, ctx, body.test_id, draft, settings, connection_id=None,
                                  force_reason=body.force_reason, request=request)  # fmt: skip
            taken = conn.execute(
                "SELECT 1 FROM app.connections "
                "WHERE workspace_id = %s AND lower(name) = lower(%s) AND deleted_at IS NULL",
                (ctx.workspace_id, body.name.strip()),
            ).fetchone()
            if taken:
                raise ApiError(409, "NAME_TAKEN", "A connection with this name already exists.")
            row = service.insert_connection(
                conn, workspace_id=ctx.workspace_id, draft=draft, name=body.name.strip(),
                environment=body.environment, tags=body.tags, color=body.color,
                check_interval_s=body.check_interval_s, created_by=ctx.user_id,
                capabilities=list(test["capabilities"]), server_version=test["server_version"],
                keyring=service.keyring_for(settings),
            )  # fmt: skip
            conn.execute(
                "UPDATE app.connection_tests SET connection_id = %s WHERE id = %s",
                (row["id"], body.test_id),
            )
            service.enqueue_initial_jobs(conn, row["id"], ctx.workspace_id)
            audit.record(conn, "connection.create", request=request, workspace_id=ctx.workspace_id,
                         actor_user_id=ctx.user_id, target_type="connection", target_id=row["id"],
                         metadata={"engine": draft.engine, "display_url": draft.display_url,
                                   "environment": body.environment})  # fmt: skip
    return _conn_out(row)


@router.get("/workspaces/{ws}/connections", response_model=list[s.ConnectionOut])
def list_connections(
    ctx: Viewer,
    db: MetaDbDep,
    state: str | None = None,
    engine: str | None = None,
    tag: str | None = None,
    env: str | None = None,
) -> list[s.ConnectionOut]:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        rows = conn.execute(
            """SELECT c.*, true AS has_secret,
                      (SELECT array_agg(latency_ms ORDER BY taken_at)
                         FROM (SELECT latency_ms, taken_at FROM app.status_snapshots
                                WHERE connection_id = c.id ORDER BY taken_at DESC LIMIT 20) t) AS spark
                 FROM app.connections c
                WHERE c.workspace_id = %(ws)s AND c.deleted_at IS NULL
                  AND (%(state)s::text IS NULL OR c.current_state::text = %(state)s)
                  AND (%(engine)s::text IS NULL OR c.engine::text = %(engine)s)
                  AND (%(tag)s::text IS NULL OR %(tag)s = ANY(c.tags))
                  AND (%(env)s::text IS NULL OR c.environment = %(env)s)
                ORDER BY c.is_demo, c.name""",
            {"ws": ctx.workspace_id, "state": state, "engine": engine, "tag": tag, "env": env},
        ).fetchall()
    return [_conn_out(r, r["spark"] or []) for r in rows]


@router.get("/workspaces/{ws}/connections/{connection_id}", response_model=s.ConnectionOut)
def get_connection(connection_id: UUID, ctx: Viewer, db: MetaDbDep) -> s.ConnectionOut:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        return _conn_out(_get(conn, ctx.workspace_id, connection_id))


@router.patch("/workspaces/{ws}/connections/{connection_id}", response_model=s.ConnectionOut)
def update_connection(connection_id: UUID, body: s.ConnectionPatch, ctx: Editor, request: Request,
                      db: MetaDbDep) -> s.ConnectionOut:  # fmt: skip
    """Name, environment, tags, colour, interval. Host/port/database/TLS/user changes go through
    rotate-secret, which re-tests first (FR-18)."""
    changes = body.model_dump(exclude_unset=True)
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = _get(conn, ctx.workspace_id, connection_id)
        if row["is_demo"] and ("environment" in changes):
            raise ApiError(
                422, "validation_error", "The demo connection's environment can't change."
            )
        if "name" in changes and changes["name"]:
            changes["name"] = changes["name"].strip()
            clash = conn.execute(
                """SELECT 1 FROM app.connections WHERE workspace_id = %s AND lower(name) = lower(%s)
                      AND id <> %s AND deleted_at IS NULL""",
                (ctx.workspace_id, changes["name"], connection_id),
            ).fetchone()
            if clash:
                raise ApiError(409, "NAME_TAKEN", "A connection with this name already exists.")
        if changes:
            sets = ", ".join(f"{k} = %({k})s" for k in changes)
            row = conn.execute(f"UPDATE app.connections SET {sets}, updated_at = now() WHERE id = %(id)s RETURNING *",
                               {**changes, "id": connection_id}).fetchone()  # fmt: skip
            if "check_interval_s" in changes:
                conn.execute("UPDATE app.schedules SET interval_s = %s, next_run_at = LEAST(next_run_at, now() + "
                             "make_interval(secs => %s)) WHERE connection_id = %s AND job_type = 'health_check'",
                             (changes["check_interval_s"], changes["check_interval_s"], connection_id))  # fmt: skip
            audit.record(conn, "connection.update", request=request, workspace_id=ctx.workspace_id,
                         actor_user_id=ctx.user_id, target_type="connection", target_id=connection_id,
                         metadata=changes)  # fmt: skip
    return _conn_out({**row, "has_secret": True})


@router.post("/workspaces/{ws}/connections/{connection_id}/test", response_model=s.TestOut)
def retest_connection(connection_id: UUID, ctx: Editor, request: Request, db: MetaDbDep,
                      settings: LensSettingsDep) -> s.TestOut:  # fmt: skip
    with _errors():
        keyring = service.keyring_for(settings)
        with db.tenant_transaction(ctx.workspace_id) as conn:
            _check_test_rate(conn, ctx.workspace_id)
            row = _get(conn, ctx.workspace_id, connection_id)
            secret = service.load_secret(conn, keyring, ctx.workspace_id, connection_id)
        if secret is None:
            raise ApiError(409, "SECRET_UNAVAILABLE", "This connection has no stored credentials.")
        draft = service.draft_from_row(row, secret)
        result = service.run_test(draft, workspace_settings=ctx.workspace["settings"], settings=settings,
                                  connection_id=str(connection_id), trusted=row["is_demo"])  # fmt: skip
        with db.tenant_transaction(ctx.workspace_id) as conn:
            test_id = service.record_test(conn, workspace_id=ctx.workspace_id, connection_id=connection_id,
                                          user_id=ctx.user_id, result=result,
                                          fingerprint=keyring.fingerprint(draft.fingerprint_data()))  # fmt: skip
            if result.ok:
                conn.execute("UPDATE app.connections SET capabilities = %s, server_version = %s WHERE id = %s",
                             (sorted(result.capabilities), result.server_version, connection_id))  # fmt: skip
        return _test_out(test_id, result, draft.engine, draft.username, draft.database)


@router.post(
    "/workspaces/{ws}/connections/{connection_id}/rotate-secret", response_model=s.ConnectionOut
)
def rotate_secret(connection_id: UUID, body: s.RotateIn, ctx: Admin, request: Request, db: MetaDbDep,
                  settings: LensSettingsDep) -> s.ConnectionOut:  # fmt: skip
    """FR-18: the new config + secret must pass a test first; the old DEK is destroyed on success."""
    with _errors():
        draft = service.resolve_draft(body.draft.model_dump(exclude={"secret"}),
                                      body.draft.secret.model_dump(exclude_none=True))  # fmt: skip
        with db.tenant_transaction(ctx.workspace_id) as conn:
            row = _get(conn, ctx.workspace_id, connection_id)
            if draft.engine != row["engine"]:
                raise ApiError(422, "validation_error", "The engine of a connection can't change.")
            test = _verified_test(conn, ctx, body.test_id, draft, settings, connection_id=connection_id,
                                  force_reason=None, request=request)  # fmt: skip
            row = conn.execute(
                """UPDATE app.connections SET host = %s, port = %s, database = %s, username = %s, options = %s,
                          tls_mode = %s, display_url = %s, capabilities = %s,
                          server_version = COALESCE(%s, server_version), updated_at = now()
                    WHERE id = %s RETURNING *""",
                (draft.host, draft.port, draft.database, draft.username, json.dumps(draft.options), draft.tls_mode,
                 draft.display_url, list(test["capabilities"]), test["server_version"], connection_id),
            ).fetchone()  # fmt: skip
            service.store_secret(
                conn, service.keyring_for(settings), ctx.workspace_id, connection_id, draft.secret
            )
            audit.record(conn, "secret.rotate", request=request, workspace_id=ctx.workspace_id,
                         actor_user_id=ctx.user_id, target_type="connection", target_id=connection_id,
                         metadata={"display_url": draft.display_url})  # fmt: skip
    return _conn_out({**row, "has_secret": True})


def _set_paused(
    connection_id: UUID, ctx: WorkspaceContext, request: Request, db, paused: bool
) -> s.ConnectionOut:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _get(conn, ctx.workspace_id, connection_id)
        row = conn.execute(
            """UPDATE app.connections SET is_paused = %s, current_state = CASE WHEN %s THEN 'paused'::app.conn_state
                      ELSE 'unknown'::app.conn_state END, last_state_change_at = now(), updated_at = now()
                WHERE id = %s RETURNING *""",
            (paused, paused, connection_id),
        ).fetchone()
        if not paused:  # check soon after resuming
            conn.execute(
                "UPDATE app.schedules SET next_run_at = now() WHERE connection_id = %s",
                (connection_id,),
            )
        audit.record(conn, "connection.pause" if paused else "connection.resume", request=request,
                     workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id, target_type="connection",
                     target_id=connection_id)  # fmt: skip
    return _conn_out({**row, "has_secret": True})


@router.post("/workspaces/{ws}/connections/{connection_id}/pause", response_model=s.ConnectionOut)
def pause(connection_id: UUID, ctx: Editor, request: Request, db: MetaDbDep) -> s.ConnectionOut:
    return _set_paused(connection_id, ctx, request, db, True)


@router.post("/workspaces/{ws}/connections/{connection_id}/resume", response_model=s.ConnectionOut)
def resume(connection_id: UUID, ctx: Editor, request: Request, db: MetaDbDep) -> s.ConnectionOut:
    return _set_paused(connection_id, ctx, request, db, False)


@router.delete("/workspaces/{ws}/connections/{connection_id}", response_model=s.ConnectionOut)
def delete_connection(
    connection_id: UUID, ctx: Admin, request: Request, db: MetaDbDep
) -> s.ConnectionOut:
    """FR-17: soft delete, crypto-shred the secret now; snapshots go after the restore window."""
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _get(conn, ctx.workspace_id, connection_id)
        conn.execute(
            "DELETE FROM app.connection_secrets WHERE connection_id = %s", (connection_id,)
        )
        conn.execute("DELETE FROM app.schedules WHERE connection_id = %s", (connection_id,))
        row = conn.execute("UPDATE app.connections SET deleted_at = now(), updated_at = now() "
                           "WHERE id = %s RETURNING *",
                           (connection_id,)).fetchone()  # fmt: skip
        audit.record(conn, "connection.delete", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="connection", target_id=connection_id)  # fmt: skip
    return _conn_out({**row, "has_secret": False})


@router.post("/workspaces/{ws}/connections/{connection_id}/restore", response_model=s.ConnectionOut)
def restore_connection(connection_id: UUID, body: s.RotateIn, ctx: Admin, request: Request, db: MetaDbDep,
                       settings: LensSettingsDep) -> s.ConnectionOut:  # fmt: skip
    """Within 7 days of deletion. The secret was shredded, so a freshly tested one is required."""
    with _errors():
        draft = service.resolve_draft(body.draft.model_dump(exclude={"secret"}),
                                      body.draft.secret.model_dump(exclude_none=True))  # fmt: skip
        with db.tenant_transaction(ctx.workspace_id) as conn:
            row = _get(conn, ctx.workspace_id, connection_id, include_deleted=True)
            if row["deleted_at"] is None:
                raise ApiError(409, "NOT_DELETED", "This connection isn't deleted.")
            if row["deleted_at"] < datetime.now(timezone.utc) - timedelta(days=RESTORE_WINDOW_DAYS):
                raise ApiError(410, "RESTORE_EXPIRED", "The 7-day restore window has passed.")
            if draft.engine != row["engine"]:
                raise ApiError(422, "validation_error", "The engine of a connection can't change.")
            taken = conn.execute(
                "SELECT 1 FROM app.connections "
                "WHERE workspace_id = %s AND lower(name) = lower(%s) AND deleted_at IS NULL",
                (ctx.workspace_id, row["name"]),
            ).fetchone()
            if taken:
                raise ApiError(
                    409,
                    "NAME_TAKEN",
                    "Another live connection now uses this name; rename it first.",
                )
            _verified_test(conn, ctx, body.test_id, draft, settings, connection_id=connection_id, force_reason=None,
                           request=request)  # fmt: skip
            row = conn.execute(
                """UPDATE app.connections SET deleted_at = NULL, host = %s, port = %s, database = %s, username = %s,
                          options = %s, tls_mode = %s, display_url = %s, current_state = 'unknown', updated_at = now()
                    WHERE id = %s RETURNING *""",
                (draft.host, draft.port, draft.database, draft.username, json.dumps(draft.options), draft.tls_mode,
                 draft.display_url, connection_id),
            ).fetchone()  # fmt: skip
            service.store_secret(
                conn, service.keyring_for(settings), ctx.workspace_id, connection_id, draft.secret
            )
            service.set_schedule(conn, connection_id, row["check_interval_s"])
            audit.record(conn, "connection.restore", request=request, workspace_id=ctx.workspace_id,
                         actor_user_id=ctx.user_id, target_type="connection", target_id=connection_id)  # fmt: skip
    return _conn_out({**row, "has_secret": True})


@router.get(
    "/workspaces/{ws}/connections/{connection_id}/grant-script", response_model=s.GrantScript
)
def grant_script(connection_id: UUID, ctx: Editor, db: MetaDbDep) -> s.GrantScript:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = _get(conn, ctx.workspace_id, connection_id)
    with _errors():
        script = get_adapter_class(row["engine"]).readonly_grant_script(
            row["database"] or "", "lens_ro"
        )
    return s.GrantScript(engine=row["engine"], script=script)


# ------------------------------------------------------------------------- status
@router.get("/workspaces/{ws}/status/overview", response_model=s.OverviewOut)
def status_overview(ctx: Viewer, db: MetaDbDep, settings: LensSettingsDep) -> s.OverviewOut:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        rows = conn.execute(
            "SELECT c.*, true AS has_secret FROM app.connections c WHERE workspace_id = %s AND deleted_at IS NULL",
            (ctx.workspace_id,),
        ).fetchall()
    by_state: dict[str, int] = {}
    for r in rows:
        by_state[r["current_state"]] = by_state.get(r["current_state"], 0) + 1
    order = {"down": 0, "unreachable": 0, "auth_failed": 0, "tls_error": 0, "degraded": 1, "unknown": 2,
             "healthy": 3, "paused": 4}  # fmt: skip
    worst = sorted(rows, key=lambda r: (order.get(r["current_state"], 2), r["health_score"] or 0))[
        :5
    ]
    return s.OverviewOut(total=len(rows), by_state=by_state, worst=[_conn_out(r) for r in worst],
                         egress_ips=[ip.strip() for ip in settings.egress_ips.split(",") if ip.strip()])  # fmt: skip


def _snapshot_out(row: dict | None) -> s.SnapshotOut | None:
    return s.SnapshotOut(**{k: row[k] for k in s.SnapshotOut.model_fields}) if row else None


@router.get("/workspaces/{ws}/connections/{connection_id}/status", response_model=s.StatusOut)
def connection_status(connection_id: UUID, ctx: Viewer, db: MetaDbDep) -> s.StatusOut:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = _get(conn, ctx.workspace_id, connection_id)
        latest = conn.execute(
            "SELECT * FROM app.status_snapshots WHERE connection_id = %s ORDER BY taken_at DESC LIMIT 1",
            (connection_id,),
        ).fetchone()
        day = conn.execute(
            "SELECT state::text AS state FROM app.status_snapshots "
            "WHERE connection_id = %s AND taken_at > now() - interval '24 hours'",
            (connection_id,),
        ).fetchall()
    return s.StatusOut(
        connection=_conn_out(row), latest=_snapshot_out(latest), uptime_24h=uptime(day)
    )


@router.post("/workspaces/{ws}/connections/{connection_id}/status/check", response_model=s.CheckQueued,
             status_code=status.HTTP_202_ACCEPTED)  # fmt: skip
def check_now(connection_id: UUID, ctx: Editor, request: Request, db: MetaDbDep) -> s.CheckQueued:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = _get(conn, ctx.workspace_id, connection_id)
        if row["is_paused"]:
            raise ApiError(409, "CONNECTION_PAUSED", "Resume the connection to check it.")
        bucket = int(datetime.now(timezone.utc).timestamp()) // CHECK_NOW_BUCKET_S
        job_id = queue.enqueue(conn, "health_check", connection_id=connection_id, workspace_id=ctx.workspace_id,
                               idempotency_key=f"health_check:{connection_id}:manual:{bucket}")  # fmt: skip
        if job_id is None:
            raise ApiError(429, "RATE_LIMITED", "A check was just requested; retry in a few seconds.",
                           {"retry_after_s": CHECK_NOW_BUCKET_S})  # fmt: skip
        audit.record(conn, "status.check", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="connection", target_id=connection_id)  # fmt: skip
    return s.CheckQueued(job_id=job_id)


@router.get(
    "/workspaces/{ws}/connections/{connection_id}/status/history",
    response_model=list[s.HistoryPoint],
)
def status_history(
    connection_id: UUID,
    ctx: Viewer,
    db: MetaDbDep,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    grain: Literal["raw", "hour", "day"] = "raw",
) -> list[s.HistoryPoint]:
    to = to or datetime.now(timezone.utc)
    from_ = from_ or to - timedelta(hours=24)
    if to - from_ > timedelta(days=90):
        raise ApiError(422, "validation_error", "The window can be at most 90 days.")
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _get(conn, ctx.workspace_id, connection_id)
        if grain == "raw":
            rows = conn.execute(
                """SELECT taken_at AS at, state::text AS state, latency_ms, health_score,
                          (state IN ('healthy', 'degraded'))::int AS up_checks, 1 AS total_checks
                     FROM app.status_snapshots
                    WHERE connection_id = %s AND taken_at >= %s AND taken_at < %s
                    ORDER BY taken_at LIMIT 5000""",
                (connection_id, from_, to),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT date_trunc(%s, taken_at) AS at, avg(latency_ms) AS latency_ms,
                          avg(health_score) AS health_score,
                          count(*) FILTER (WHERE state IN ('healthy', 'degraded')) AS up_checks,
                          count(*) FILTER (WHERE state <> 'paused') AS total_checks
                     FROM app.status_snapshots
                    WHERE connection_id = %s AND taken_at >= %s AND taken_at < %s
                    GROUP BY 1 ORDER BY 1""",
                (grain, connection_id, from_, to),
            ).fetchall()
    return [s.HistoryPoint(**r) for r in rows]


WINDOWS = {
    "24h": (timedelta(hours=24), "hour"),
    "7d": (timedelta(days=7), "day"),
    "30d": (timedelta(days=30), "day"),
}


@router.get("/workspaces/{ws}/connections/{connection_id}/uptime", response_model=s.UptimeOut)
def uptime_bars(connection_id: UUID, ctx: Viewer, db: MetaDbDep,
                window: Literal["24h", "7d", "30d"] = "24h") -> s.UptimeOut:  # fmt: skip
    span, grain = WINDOWS[window]
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _get(conn, ctx.workspace_id, connection_id)
        rows = conn.execute(
            """SELECT date_trunc(%s, taken_at) AS start,
                      count(*) FILTER (WHERE state IN ('healthy', 'degraded')) AS up,
                      count(*) FILTER (WHERE state <> 'paused') AS total
                 FROM app.status_snapshots
                WHERE connection_id = %s AND taken_at > now() - %s
                GROUP BY 1 ORDER BY 1""",
            (grain, connection_id, span),
        ).fetchall()
    up, total = sum(r["up"] for r in rows), sum(r["total"] for r in rows)
    bars = [{"start": r["start"], "checks": r["total"],
             "uptime_pct": round(100.0 * r["up"] / r["total"], 2) if r["total"] else None} for r in rows]  # fmt: skip
    return s.UptimeOut(window=window, uptime_pct=round(100.0 * up / total, 2) if total else None, checks=total,
                       bars=bars)  # fmt: skip
