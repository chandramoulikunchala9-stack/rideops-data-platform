"""Alert evaluation (§14.2, FR-41): evaluate a connection's rules after every snapshot / sync,
open one event per episode (dedup key = rule|connection), auto-resolve after 2 healthy
evaluations, open incidents for critical alerts, and fan notifications out as retried jobs."""

import json
import logging
from typing import Any

from rideops_api.jobs import queue
from rideops_api.jobs.registry import JobContext, job
from rideops_api.lens.alerts import channels, conditions
from rideops_api.lens.connections.service import keyring_for
from rideops_api.settings import get_lens_settings

logger = logging.getLogger(__name__)
RESOLVE_AFTER_OK = 2  # flapping guard


def default_rules(conn, workspace_id: Any, connection_id: Any, created_by: Any = None) -> None:
    for r in conditions.DEFAULT_RULES:
        conn.execute(
            """INSERT INTO app.alert_rules (workspace_id, connection_id, name, rule_type, condition, severity,
                      is_default, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, true, %s)""",
            (workspace_id, connection_id, r["name"], r["rule_type"], json.dumps(r["condition"]), r["severity"],
             created_by),
        )  # fmt: skip


def _context(conn, c: dict[str, Any], rules: list[dict[str, Any]]) -> conditions.EvalContext:
    snaps = conn.execute(
        """SELECT state::text AS state, reachable, latency_ms, metrics, error_code FROM app.status_snapshots
            WHERE connection_id = %s ORDER BY taken_at DESC LIMIT 10""",
        (c["id"],),
    ).fetchall()
    tables = sorted({r["condition"].get("freshness_table") or r["condition"].get("row_drop_table")
                     for r in rules if r["rule_type"] in ("freshness", "row_drop")} - {None})  # fmt: skip
    stats: dict[str, list[dict[str, Any]]] = {}
    for t in tables:
        schema, _, name = t.rpartition(".")
        stats[t] = conn.execute(
            """SELECT taken_at, row_estimate, freshness_max FROM app.table_stats_snapshots
                WHERE connection_id = %s AND schema_name = %s AND table_name = %s
                ORDER BY taken_at DESC LIMIT 400""",
            (c["id"], schema or "public", name),
        ).fetchall()
    changes = conn.execute(
        "SELECT change_type, object_ref, detected_at FROM app.schema_changes WHERE connection_id = %s "
        "ORDER BY detected_at DESC LIMIT 200",
        (c["id"],),
    ).fetchall()
    test = conn.execute(
        "SELECT privilege_findings FROM app.connection_tests WHERE connection_id = %s AND ok "
        "ORDER BY created_at DESC LIMIT 1",
        (c["id"],),
    ).fetchone()
    return conditions.EvalContext(connection=c, snapshots=snaps, table_stats=stats, schema_changes=changes,
                                  privilege_findings=(test or {}).get("privilege_findings") or [])  # fmt: skip


def evaluate(db: Any, connection_id: Any) -> dict[str, int]:
    counts = {"fired": 0, "resolved": 0}
    with db.transaction() as conn:
        c = conn.execute("SELECT * FROM app.connections WHERE id = %s AND deleted_at IS NULL",
                         (connection_id,)).fetchone()  # fmt: skip
        if c is None or c["is_paused"]:
            return counts
        in_maintenance = conn.execute(
            """SELECT 1 FROM app.maintenance_windows WHERE workspace_id = %s
                  AND (connection_id IS NULL OR connection_id = %s) AND now() BETWEEN starts_at AND ends_at""",
            (c["workspace_id"], connection_id),
        ).fetchone()
        if in_maintenance:
            return counts  # suppression: no firing and no resolving while maintenance runs
        rules = conn.execute(
            """SELECT * FROM app.alert_rules WHERE workspace_id = %s AND is_enabled
                  AND (connection_id IS NULL OR connection_id = %s)""",
            (c["workspace_id"], connection_id),
        ).fetchall()
        if not rules:
            return counts
        ctx = _context(conn, c, rules)
        for rule in rules:
            outcome = conditions.EVALUATORS[rule["rule_type"]](rule["condition"], ctx)
            if outcome.breached is None:
                continue
            key = f"{rule['id']}|{connection_id}"
            open_ev = conn.execute(
                "SELECT * FROM app.alert_events WHERE dedup_key = %s AND status IN ('firing', 'acknowledged', 'muted')",
                (key,),
            ).fetchone()
            value = json.dumps(outcome.value, default=str)
            if outcome.breached:
                if open_ev:
                    conn.execute(
                        """UPDATE app.alert_events SET ok_streak = 0, last_value = %s, summary = %s,
                                  status = CASE WHEN status = 'muted' AND muted_until <= now() THEN 'firing'
                                                ELSE status END
                            WHERE id = %s""",
                        (value, outcome.summary, open_ev["id"]),
                    )
                    continue
                incident_id = None
                if rule["severity"] == "critical":
                    incident_id = conn.execute(
                        """INSERT INTO app.incidents (workspace_id, connection_id, title, severity, runbook_url)
                           VALUES (%s, %s, %s, 'critical', %s) RETURNING id""",
                        (
                            c["workspace_id"],
                            connection_id,
                            outcome.summary[:200],
                            rule["runbook_url"],
                        ),
                    ).fetchone()["id"]
                    conn.execute(
                        "INSERT INTO app.incident_notes (incident_id, kind, body) VALUES (%s, 'event', %s)",
                        (incident_id, f"Opened by alert rule “{rule['name']}”: {outcome.summary}"),
                    )
                ev = conn.execute(
                    """INSERT INTO app.alert_events (workspace_id, rule_id, connection_id, dedup_key, status, severity,
                              last_value, summary, incident_id)
                       VALUES (%s, %s, %s, %s, 'firing', %s, %s, %s, %s)
                       ON CONFLICT (dedup_key) WHERE status IN ('firing', 'acknowledged', 'muted') DO NOTHING
                       RETURNING id""",
                    (c["workspace_id"], rule["id"], connection_id, key, rule["severity"], value, outcome.summary,
                     incident_id),
                ).fetchone()  # fmt: skip
                if ev:
                    counts["fired"] += 1
                    _fan_out(conn, c["workspace_id"], ev["id"], "alert.firing", rule)
            elif open_ev:
                streak = open_ev["ok_streak"] + 1
                if streak < RESOLVE_AFTER_OK:
                    conn.execute("UPDATE app.alert_events SET ok_streak = %s, last_value = %s WHERE id = %s",
                                 (streak, value, open_ev["id"]))  # fmt: skip
                    continue
                conn.execute(
                    """UPDATE app.alert_events SET status = 'resolved', resolved_at = now(), ok_streak = %s,
                              last_value = %s WHERE id = %s""",
                    (streak, value, open_ev["id"]),
                )
                counts["resolved"] += 1
                if open_ev["incident_id"]:
                    _maybe_resolve_incident(conn, open_ev["incident_id"])
                if open_ev["status"] != "muted":
                    _fan_out(conn, c["workspace_id"], open_ev["id"], "alert.resolved", rule)
    return counts


def _maybe_resolve_incident(conn, incident_id: Any) -> None:
    still_open = conn.execute(
        "SELECT 1 FROM app.alert_events WHERE incident_id = %s AND status IN ('firing', 'acknowledged', 'muted')",
        (incident_id,),
    ).fetchone()
    if not still_open:
        conn.execute("UPDATE app.incidents SET status = 'resolved', resolved_at = now() "
                     "WHERE id = %s AND status <> 'resolved'",
                     (incident_id,))  # fmt: skip
        conn.execute("INSERT INTO app.incident_notes (incident_id, kind, body) VALUES (%s, 'event', %s)",
                     (incident_id, "All linked alerts recovered; incident auto-resolved."))  # fmt: skip


def _fan_out(conn, workspace_id: Any, event_id: int, event: str, rule: dict[str, Any]) -> None:
    """In-app for every member now (same transaction); one retried job per external channel."""
    ev = conn.execute(
        """SELECT e.*, c.name AS connection_name FROM app.alert_events e
             LEFT JOIN app.connections c ON c.id = e.connection_id WHERE e.id = %s""",
        (event_id,),
    ).fetchone()
    title = ("Resolved: " if event == "alert.resolved" else f"{ev['severity'].upper()}: ") + rule[
        "name"
    ]
    conn.execute(
        """INSERT INTO app.notifications (workspace_id, user_id, kind, title, body, link)
           SELECT %s, user_id, %s, %s, %s, %s FROM app.memberships WHERE workspace_id = %s""",
        (workspace_id, event, title, ev["summary"], f"/lens/alerts?alert={event_id}", workspace_id),
    )
    for channel_id in rule["channels"] or []:
        queue.enqueue(conn, "notify", workspace_id=workspace_id,
                      payload={"event_id": event_id, "event": event, "channel_id": str(channel_id)},
                      idempotency_key=f"notify:{event_id}:{event}:{channel_id}")  # fmt: skip


def payload_for(conn, event_id: int, event: str, settings) -> dict[str, Any]:
    ev = conn.execute(
        """SELECT e.*, r.name AS rule_name, c.name AS cname, c.engine::text AS engine, c.environment
             FROM app.alert_events e JOIN app.alert_rules r ON r.id = e.rule_id
             LEFT JOIN app.connections c ON c.id = e.connection_id WHERE e.id = %s""",
        (event_id,),
    ).fetchone()
    return {
        "event": event, "alert_id": ev["id"], "severity": ev["severity"], "rule": ev["rule_name"],
        "connection": {"id": str(ev["connection_id"]), "name": ev["cname"], "engine": ev["engine"],
                       "environment": ev["environment"]},
        "summary": ev["summary"], "opened_at": ev["opened_at"], "resolved_at": ev["resolved_at"],
        "link": f"{settings.public_app_url.rstrip('/')}/app/lens/connections/{ev['connection_id']}",
    }  # fmt: skip


@job("alert_eval", timeout_s=5, max_attempts=3, priority=95)
def alert_eval_job(ctx: JobContext) -> None:
    evaluate(ctx.db, ctx.job["connection_id"])


@job("notify", timeout_s=10, max_attempts=5, priority=95)
def notify_job(ctx: JobContext) -> None:
    """One channel, one event. Raising makes the queue retry with exponential backoff (FR-42)."""
    settings = ctx.settings or get_lens_settings()
    p = ctx.payload
    with ctx.db.pool.connection() as conn:
        ch = conn.execute(
            "SELECT * FROM app.notification_channels WHERE id = %s", (p["channel_id"],)
        ).fetchone()
        if ch is None or ch["kind"] == "inapp":
            return
        ws = conn.execute(
            "SELECT settings FROM app.workspaces WHERE id = %s", (ch["workspace_id"],)
        ).fetchone()
        payload = payload_for(conn, p["event_id"], p["event"], settings)
    config = channels.decrypt_config(keyring_for(settings), ch)
    channels.deliver(
        ch["kind"], config, payload, workspace_settings=ws["settings"], settings=settings
    )
