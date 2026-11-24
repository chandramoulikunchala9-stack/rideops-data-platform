"""Alerts, rules, channels, incidents, maintenance windows, notifications (§13.7)."""

import json
import secrets
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from rideops_api import audit
from rideops_api.auth.deps import LensSettingsDep, MetaDbDep, SessionDep
from rideops_api.errors import ApiError
from rideops_api.lens.alerts import channels, conditions
from rideops_api.lens.connections.service import keyring_for
from rideops_api.lens.security import netguard
from rideops_api.lens.security.crypto import KeyRingError
from rideops_api.workspaces.deps import WorkspaceContext, require

router = APIRouter(tags=["alerts"])
Viewer = Annotated[WorkspaceContext, require("connection.view")]
Editor = Annotated[WorkspaceContext, require("alert_rule.edit")]
ChannelAdmin = Annotated[WorkspaceContext, require("channels.manage")]
W = "/workspaces/{ws}"
Severity = Literal["info", "warning", "critical"]


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    connection_id: UUID | None = None
    rule_type: Literal["status", "metric", "freshness", "row_drop", "schema_change", "privilege"]
    condition: dict[str, Any]
    severity: Severity = "warning"
    channels: list[UUID] = Field(default_factory=list, max_length=20)
    runbook_url: str | None = Field(default=None, max_length=500, pattern=r"^https?://")
    is_enabled: bool = True


class RulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    condition: dict[str, Any] | None = None
    severity: Severity | None = None
    channels: list[UUID] | None = Field(default=None, max_length=20)
    runbook_url: str | None = Field(default=None, max_length=500, pattern=r"^https?://")
    is_enabled: bool | None = None


class ChannelIn(BaseModel):
    kind: Literal["email", "webhook", "slack"]
    name: str = Field(min_length=1, max_length=120)
    url: str | None = Field(default=None, max_length=2000)
    emails: list[str] = Field(default_factory=list, max_length=20)


class MuteIn(BaseModel):
    until: datetime


class IncidentIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    severity: Severity = "warning"
    connection_id: UUID | None = None
    runbook_url: str | None = Field(default=None, max_length=500, pattern=r"^https?://")


class IncidentPatch(BaseModel):
    status: Literal["open", "acknowledged", "resolved"]


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class WindowIn(BaseModel):
    connection_id: UUID | None = None
    starts_at: datetime
    ends_at: datetime
    reason: str | None = Field(default=None, max_length=500)


def _check_connection(conn, ws: UUID, connection_id: UUID | None) -> None:
    if (
        connection_id
        and not conn.execute(
            "SELECT 1 FROM app.connections WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL",
            (connection_id, ws),
        ).fetchone()
    ):
        raise ApiError(404, "NOT_FOUND", "Connection not found.")


def _check_channels(conn, ws: UUID, ids: list[UUID]) -> None:
    if not ids:
        return
    n = conn.execute("SELECT count(*) AS n FROM app.notification_channels WHERE workspace_id = %s AND id = ANY(%s)",
                     (ws, ids)).fetchone()["n"]  # fmt: skip
    if n != len(set(ids)):
        raise ApiError(404, "NOT_FOUND", "Notification channel not found.")


# ------------------------------------------------------------------------ rules
@router.get(W + "/alert-rules")
def list_rules(ctx: Viewer, db: MetaDbDep, connection_id: UUID | None = None) -> list[dict]:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        return conn.execute(
            """SELECT * FROM app.alert_rules WHERE workspace_id = %s
                  AND (%s::uuid IS NULL OR connection_id = %s OR connection_id IS NULL)
                ORDER BY is_default DESC, created_at""",
            (ctx.workspace_id, connection_id, connection_id),
        ).fetchall()


@router.post(W + "/alert-rules", status_code=status.HTTP_201_CREATED)
def create_rule(body: RuleIn, ctx: Editor, request: Request, db: MetaDbDep) -> dict:
    problem = conditions.validate_condition(body.rule_type, body.condition)
    if problem:
        raise ApiError(422, "validation_error", problem)
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _check_connection(conn, ctx.workspace_id, body.connection_id)
        _check_channels(conn, ctx.workspace_id, body.channels)
        row = conn.execute(
            """INSERT INTO app.alert_rules (workspace_id, connection_id, name, rule_type, condition, severity,
                      channels, runbook_url, is_enabled, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (ctx.workspace_id, body.connection_id, body.name.strip(), body.rule_type, json.dumps(body.condition),
             body.severity, body.channels, body.runbook_url, body.is_enabled, ctx.user_id),
        ).fetchone()  # fmt: skip
        audit.record(conn, "alert_rule.create", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="alert_rule", target_id=row["id"])  # fmt: skip
    return row


def _rule(conn, ws: UUID, rule_id: UUID) -> dict:
    row = conn.execute(
        "SELECT * FROM app.alert_rules WHERE id = %s AND workspace_id = %s", (rule_id, ws)
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Alert rule not found.")
    return row


@router.patch(W + "/alert-rules/{rule_id}")
def update_rule(
    rule_id: UUID, body: RulePatch, ctx: Editor, request: Request, db: MetaDbDep
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    with db.tenant_transaction(ctx.workspace_id) as conn:
        rule = _rule(conn, ctx.workspace_id, rule_id)
        if "condition" in changes:
            problem = conditions.validate_condition(rule["rule_type"], changes["condition"])
            if problem:
                raise ApiError(422, "validation_error", problem)
            changes["condition"] = json.dumps(changes["condition"])
        if "channels" in changes:
            _check_channels(conn, ctx.workspace_id, changes["channels"])
        if changes:
            sets = ", ".join(f"{k} = %({k})s" for k in changes)
            rule = conn.execute(f"UPDATE app.alert_rules SET {sets} WHERE id = %(id)s RETURNING *",
                                {**changes, "id": rule_id}).fetchone()  # fmt: skip
            audit.record(conn, "alert_rule.update", request=request, workspace_id=ctx.workspace_id,
                         actor_user_id=ctx.user_id, target_type="alert_rule", target_id=rule_id)  # fmt: skip
    return rule


@router.delete(W + "/alert-rules/{rule_id}")
def delete_rule(rule_id: UUID, ctx: Editor, request: Request, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _rule(conn, ctx.workspace_id, rule_id)
        conn.execute("DELETE FROM app.alert_rules WHERE id = %s", (rule_id,))
        audit.record(conn, "alert_rule.delete", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="alert_rule", target_id=rule_id)  # fmt: skip
    return {"deleted": True}


@router.post(W + "/alert-rules/{rule_id}/test")
def test_rule(rule_id: UUID, ctx: Editor, db: MetaDbDep, settings: LensSettingsDep) -> dict:
    """Send a test notification to every channel of the rule, synchronously, and report each result."""
    with db.tenant_transaction(ctx.workspace_id) as conn:
        rule = _rule(conn, ctx.workspace_id, rule_id)
        rows = conn.execute(
            "SELECT * FROM app.notification_channels WHERE id = ANY(%s)", (rule["channels"],)
        ).fetchall()
    payload = {"event": "test", "severity": rule["severity"], "rule": rule["name"],
               "summary": f"Test notification for rule “{rule['name']}”", "link": settings.public_app_url}  # fmt: skip
    return {"results": [_send_one(ch, payload, ctx, settings) for ch in rows]}


def _send_one(ch: dict, payload: dict, ctx: WorkspaceContext, settings) -> dict:
    try:
        config = channels.decrypt_config(keyring_for(settings), ch)
        channels.deliver(
            ch["kind"],
            config,
            payload,
            workspace_settings=ctx.workspace["settings"],
            settings=settings,
        )
        return {"channel_id": str(ch["id"]), "ok": True}
    except (channels.DeliveryError, netguard.NetworkPolicyError) as exc:
        return {
            "channel_id": str(ch["id"]),
            "ok": False,
            "error": getattr(exc, "message", str(exc)),
        }


# --------------------------------------------------------------------- channels
def _channel_out(row: dict) -> dict:
    return {k: row[k] for k in ("id", "kind", "name", "summary", "is_verified", "created_at")}


@router.get(W + "/notification-channels")
def list_channels(ctx: Viewer, db: MetaDbDep) -> list[dict]:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        rows = conn.execute("SELECT * FROM app.notification_channels WHERE workspace_id = %s ORDER BY created_at",
                            (ctx.workspace_id,)).fetchall()  # fmt: skip
    return [_channel_out(r) for r in rows]


@router.post(W + "/notification-channels", status_code=status.HTTP_201_CREATED)
def create_channel(body: ChannelIn, ctx: ChannelAdmin, request: Request, db: MetaDbDep,
                   settings: LensSettingsDep) -> dict:  # fmt: skip
    """Secrets (webhook URL tokens, the generated signing secret, recipients) are encrypted at rest and
    never returned. The signing secret is shown once in this response so receivers can verify."""
    config: dict[str, Any]
    if body.kind in ("webhook", "slack"):
        if not body.url:
            raise ApiError(422, "validation_error", "A URL is required.")
        try:
            channels.check_url(body.url, ctx.workspace["settings"], settings)
        except channels.DeliveryError as exc:
            raise ApiError(422, "validation_error", str(exc)) from None
        except netguard.NetworkPolicyError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from None
        config = {"url": body.url}
        if body.kind == "webhook":
            config["secret"] = secrets.token_urlsafe(32)
    else:
        emails = [e.strip().lower() for e in body.emails if "@" in e and len(e) <= 254]
        if not emails:
            raise ApiError(422, "validation_error", "Add at least one email address.")
        config = {"emails": emails}
    channel_id = uuid.uuid4()
    try:
        blob = channels.encrypt_config(keyring_for(settings), ctx.workspace_id, channel_id, config)
    except KeyRingError:
        raise ApiError(
            500, "LENS_KEYS_MISSING", "Server encryption keys are not configured (ops issue)."
        ) from None
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = conn.execute(
            """INSERT INTO app.notification_channels (id, workspace_id, kind, name, kek_version, wrapped_dek, nonce,
                      config_enc, summary, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (channel_id, ctx.workspace_id, body.kind, body.name.strip(), blob.kek_version, blob.wrapped_dek, blob.nonce,
             blob.ciphertext, channels.summarize(body.kind, config), ctx.user_id),
        ).fetchone()  # fmt: skip
        audit.record(conn, "channel.create", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="notification_channel", target_id=channel_id,
                     metadata={"kind": body.kind})  # fmt: skip
    out = _channel_out(row)
    if body.kind == "webhook":
        out["signing_secret"] = config["secret"]
    return out


@router.post(W + "/notification-channels/{channel_id}/verify")
def verify_channel(
    channel_id: UUID, ctx: ChannelAdmin, db: MetaDbDep, settings: LensSettingsDep
) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        ch = conn.execute("SELECT * FROM app.notification_channels WHERE id = %s AND workspace_id = %s",
                          (channel_id, ctx.workspace_id)).fetchone()  # fmt: skip
    if ch is None:
        raise ApiError(404, "NOT_FOUND", "Notification channel not found.")
    result = _send_one(ch, {"event": "test", "severity": "info", "summary": "Lens channel verification",
                            "link": settings.public_app_url}, ctx, settings)  # fmt: skip
    if result["ok"]:
        with db.tenant_transaction(ctx.workspace_id) as conn:
            conn.execute(
                "UPDATE app.notification_channels SET is_verified = true WHERE id = %s",
                (channel_id,),
            )
    return result


@router.delete(W + "/notification-channels/{channel_id}")
def delete_channel(channel_id: UUID, ctx: ChannelAdmin, request: Request, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        gone = conn.execute("DELETE FROM app.notification_channels WHERE id = %s AND workspace_id = %s RETURNING id",
                            (channel_id, ctx.workspace_id)).fetchone()  # fmt: skip
        if gone is None:
            raise ApiError(404, "NOT_FOUND", "Notification channel not found.")
        conn.execute("UPDATE app.alert_rules SET channels = array_remove(channels, %s) WHERE workspace_id = %s",
                     (channel_id, ctx.workspace_id))  # fmt: skip
        audit.record(conn, "channel.delete", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="notification_channel", target_id=channel_id)  # fmt: skip
    return {"deleted": True}


# ----------------------------------------------------------------------- alerts
@router.get(W + "/alerts")
def list_alerts(ctx: Viewer, db: MetaDbDep, status: str | None = None, severity: str | None = None,
                connection: UUID | None = None, limit: int = 100) -> list[dict]:  # fmt: skip
    with db.tenant_transaction(ctx.workspace_id) as conn:
        return conn.execute(
            """SELECT e.id, e.status, e.severity, e.summary, e.opened_at, e.acknowledged_at, e.resolved_at,
                      e.muted_until, e.connection_id, e.incident_id, e.last_value, r.name AS rule_name,
                      r.runbook_url, c.name AS connection_name
                 FROM app.alert_events e JOIN app.alert_rules r ON r.id = e.rule_id
                 LEFT JOIN app.connections c ON c.id = e.connection_id
                WHERE e.workspace_id = %(ws)s
                  AND (%(status)s::text IS NULL OR e.status = %(status)s OR (%(status)s = 'open'
                       AND e.status IN ('firing', 'acknowledged', 'muted')))
                  AND (%(sev)s::text IS NULL OR e.severity = %(sev)s)
                  AND (%(conn)s::uuid IS NULL OR e.connection_id = %(conn)s)
                ORDER BY (e.status = 'firing') DESC, e.opened_at DESC LIMIT %(limit)s""",
            {"ws": ctx.workspace_id, "status": status, "sev": severity, "conn": connection,
             "limit": max(1, min(limit, 500))},
        ).fetchall()  # fmt: skip


def _alert_action(
    alert_id: int, ctx: WorkspaceContext, db, request: Request, sql: str, params: tuple, action: str
) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        row = conn.execute(sql, (*params, alert_id, ctx.workspace_id)).fetchone()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "Open alert not found.")
        audit.record(conn, f"alert.{action}", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="alert", target_id=alert_id)  # fmt: skip
        if row.get("incident_id") and action == "resolve":
            from rideops_api.lens.alerts.engine import _maybe_resolve_incident

            _maybe_resolve_incident(conn, row["incident_id"])
    return row


@router.post(W + "/alerts/{alert_id}/ack")
def ack_alert(alert_id: int, ctx: Editor, request: Request, db: MetaDbDep) -> dict:
    return _alert_action(alert_id, ctx, db, request,
                         """UPDATE app.alert_events SET status = 'acknowledged', acknowledged_at = now(),
                                   acknowledged_by = %s WHERE id = %s AND workspace_id = %s AND status = 'firing'
                            RETURNING *""", (ctx.user_id,), "ack")  # fmt: skip


@router.post(W + "/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, ctx: Editor, request: Request, db: MetaDbDep) -> dict:
    return _alert_action(alert_id, ctx, db, request,
                         """UPDATE app.alert_events SET status = 'resolved', resolved_at = now()
                             WHERE id = %s AND workspace_id = %s AND status IN ('firing', 'acknowledged', 'muted')
                            RETURNING *""", (), "resolve")  # fmt: skip


@router.post(W + "/alerts/{alert_id}/mute")
def mute_alert(alert_id: int, body: MuteIn, ctx: Editor, request: Request, db: MetaDbDep) -> dict:
    return _alert_action(alert_id, ctx, db, request,
                         """UPDATE app.alert_events SET status = 'muted', muted_until = %s
                             WHERE id = %s AND workspace_id = %s AND status IN ('firing', 'acknowledged')
                            RETURNING *""", (body.until,), "mute")  # fmt: skip


# -------------------------------------------------------------------- incidents
def _incident(conn, ws: UUID, incident_id: UUID) -> dict:
    row = conn.execute(
        "SELECT * FROM app.incidents WHERE id = %s AND workspace_id = %s", (incident_id, ws)
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Incident not found.")
    return row


@router.get(W + "/incidents")
def list_incidents(ctx: Viewer, db: MetaDbDep, status: str | None = None) -> list[dict]:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        return conn.execute(
            """SELECT i.*, c.name AS connection_name,
                      (SELECT count(*) FROM app.alert_events e WHERE e.incident_id = i.id) AS alert_count
                 FROM app.incidents i LEFT JOIN app.connections c ON c.id = i.connection_id
                WHERE i.workspace_id = %s AND (%s::text IS NULL OR i.status = %s)
                ORDER BY (i.status <> 'resolved') DESC, i.opened_at DESC LIMIT 200""",
            (ctx.workspace_id, status, status),
        ).fetchall()


@router.post(W + "/incidents", status_code=status.HTTP_201_CREATED)
def create_incident(body: IncidentIn, ctx: Editor, request: Request, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _check_connection(conn, ctx.workspace_id, body.connection_id)
        row = conn.execute(
            """INSERT INTO app.incidents (workspace_id, connection_id, title, severity, runbook_url, created_by)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (
                ctx.workspace_id,
                body.connection_id,
                body.title.strip(),
                body.severity,
                body.runbook_url,
                ctx.user_id,
            ),
        ).fetchone()
        audit.record(conn, "incident.create", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="incident", target_id=row["id"])  # fmt: skip
    return row


@router.get(W + "/incidents/{incident_id}")
def get_incident(incident_id: UUID, ctx: Viewer, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        inc = _incident(conn, ctx.workspace_id, incident_id)
        notes = conn.execute(
            """SELECT n.id, n.kind, n.body, n.created_at, u.display_name AS author FROM app.incident_notes n
                 LEFT JOIN app.users u ON u.id = n.author_id WHERE n.incident_id = %s ORDER BY n.created_at""",
            (incident_id,),
        ).fetchall()
        alerts = conn.execute(
            "SELECT id, status, severity, summary, opened_at, resolved_at FROM app.alert_events WHERE incident_id = %s",
            (incident_id,),
        ).fetchall()
    return {**inc, "notes": notes, "alerts": alerts}


@router.patch(W + "/incidents/{incident_id}")
def update_incident(
    incident_id: UUID, body: IncidentPatch, ctx: Editor, request: Request, db: MetaDbDep
) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        before = _incident(conn, ctx.workspace_id, incident_id)
        row = conn.execute(
            """UPDATE app.incidents SET status = %(s)s,
                      acknowledged_at = CASE WHEN %(s)s = 'acknowledged' THEN coalesce(acknowledged_at, now())
                                             ELSE acknowledged_at END,
                      resolved_at = CASE WHEN %(s)s = 'resolved' THEN now() ELSE NULL END
                WHERE id = %(id)s RETURNING *""",
            {"s": body.status, "id": incident_id},
        ).fetchone()
        conn.execute("INSERT INTO app.incident_notes (incident_id, author_id, kind, body) VALUES (%s, %s, 'event', %s)",
                     (incident_id, ctx.user_id, f"Status {before['status']} → {body.status}"))  # fmt: skip
        audit.record(conn, "incident.update", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="incident", target_id=incident_id,
                     metadata={"status": body.status})  # fmt: skip
    return row


@router.post(W + "/incidents/{incident_id}/notes", status_code=status.HTTP_201_CREATED)
def add_note(incident_id: UUID, body: NoteIn, ctx: Editor, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _incident(conn, ctx.workspace_id, incident_id)
        return conn.execute(
            "INSERT INTO app.incident_notes (incident_id, author_id, body) VALUES (%s, %s, %s) RETURNING *",
            (incident_id, ctx.user_id, body.body),
        ).fetchone()


# ---------------------------------------------------------- maintenance windows
@router.get(W + "/maintenance-windows")
def list_windows(ctx: Viewer, db: MetaDbDep) -> list[dict]:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        return conn.execute("SELECT * FROM app.maintenance_windows WHERE workspace_id = %s AND ends_at > now() "
                            "ORDER BY starts_at", (ctx.workspace_id,)).fetchall()  # fmt: skip


@router.post(W + "/maintenance-windows", status_code=status.HTTP_201_CREATED)
def create_window(body: WindowIn, ctx: Editor, request: Request, db: MetaDbDep) -> dict:
    if body.ends_at <= body.starts_at:
        raise ApiError(422, "validation_error", "The window must end after it starts.")
    with db.tenant_transaction(ctx.workspace_id) as conn:
        _check_connection(conn, ctx.workspace_id, body.connection_id)
        row = conn.execute(
            """INSERT INTO app.maintenance_windows (workspace_id, connection_id, starts_at, ends_at, reason, created_by)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (
                ctx.workspace_id,
                body.connection_id,
                body.starts_at,
                body.ends_at,
                body.reason,
                ctx.user_id,
            ),
        ).fetchone()
        audit.record(conn, "maintenance.create", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="maintenance_window", target_id=row["id"])  # fmt: skip
    return row


@router.delete(W + "/maintenance-windows/{window_id}")
def delete_window(window_id: UUID, ctx: Editor, db: MetaDbDep) -> dict:
    with db.tenant_transaction(ctx.workspace_id) as conn:
        gone = conn.execute("DELETE FROM app.maintenance_windows WHERE id = %s AND workspace_id = %s RETURNING id",
                            (window_id, ctx.workspace_id)).fetchone()  # fmt: skip
    if gone is None:
        raise ApiError(404, "NOT_FOUND", "Maintenance window not found.")
    return {"deleted": True}


# ---------------------------------------------------------------- notifications
@router.get("/notifications")
def my_notifications(session: SessionDep, db: MetaDbDep, unread: bool = False) -> dict:
    with db.pool.connection() as conn:
        items = conn.execute(
            """SELECT id, workspace_id, kind, title, body, link, read_at, created_at FROM app.notifications
                WHERE user_id = %s AND (NOT %s OR read_at IS NULL) ORDER BY created_at DESC LIMIT 50""",
            (session.user_id, unread),
        ).fetchall()
        count = conn.execute("SELECT count(*) AS n FROM app.notifications WHERE user_id = %s AND read_at IS NULL",
                             (session.user_id,)).fetchone()["n"]  # fmt: skip
    return {"items": items, "unread": count}


@router.post("/notifications/read-all")
def read_all(session: SessionDep, db: MetaDbDep) -> dict:
    with db.pool.connection() as conn:
        conn.execute("UPDATE app.notifications SET read_at = now() WHERE user_id = %s AND read_at IS NULL",
                     (session.user_id,))  # fmt: skip
    return {"unread": 0}
