"""`health_check` job (§14.2): load → skip if paused → decrypt → guard → adapter.status() →
state + score → snapshot → connection summary. One target session, fixed read-only queries (FR-21)."""

import json
import logging
from datetime import datetime
from typing import Any

from rideops_api.jobs import queue
from rideops_api.jobs.registry import JobContext, job
from rideops_api.lens import pools
from rideops_api.lens.adapters.base import StatusSnapshot, now
from rideops_api.lens.adapters.registry import AdapterUnavailable, get_adapter_class
from rideops_api.lens.connections import service
from rideops_api.lens.security.crypto import DecryptError, KeyRingError
from rideops_api.lens.status import scoring
from rideops_api.settings import get_lens_settings

logger = logging.getLogger(__name__)


def _failed(code: str, message: str, stage: str) -> StatusSnapshot:
    return StatusSnapshot(taken_at=now(), reachable=False, latency_ms=None, error_code=code,
                          error_message=message, error_stage=stage)  # fmt: skip


def check_connection(db: Any, connection_id: Any, settings=None) -> dict[str, Any] | None:
    """Run one health check and persist it. Returns the snapshot row, or None if skipped."""
    settings = settings or get_lens_settings()
    with db.pool.connection() as conn:
        row = conn.execute(
            """SELECT c.*, w.settings AS ws_settings FROM app.connections c
                 JOIN app.workspaces w ON w.id = c.workspace_id
                WHERE c.id = %s AND c.deleted_at IS NULL AND w.deleted_at IS NULL""",
            (connection_id,),
        ).fetchone()
        if row is None or row["is_paused"]:
            return None  # FR-19: paused connections are never contacted
        try:
            keyring = service.keyring_for(settings)
            secret = service.load_secret(conn, keyring, row["workspace_id"], row["id"])
        except (KeyRingError, DecryptError) as exc:
            logger.error("Cannot decrypt secret for connection %s: %s", connection_id, exc)
            secret = None
        previous = conn.execute(
            """SELECT taken_at, metrics FROM app.status_snapshots
                WHERE connection_id = %s AND reachable ORDER BY taken_at DESC LIMIT 1""",
            (connection_id,),
        ).fetchone()
        recent = conn.execute(
            """SELECT reachable, latency_ms FROM app.status_snapshots
                WHERE connection_id = %s ORDER BY taken_at DESC LIMIT 10""",
            (connection_id,),
        ).fetchall()

    if secret is None:
        snap = _failed(
            "SECRET_UNAVAILABLE", "The stored credentials could not be decrypted.", "SECRET"
        )
    else:
        snap = _probe(row, secret, settings)

    metrics = scoring.with_deltas(
        snap.metrics, previous["metrics"] if previous else None,
        (snap.taken_at - previous["taken_at"]).total_seconds() if previous else None,
    )  # fmt: skip
    failed = sum(1 for r in recent if not r["reachable"]) + (0 if snap.reachable else 1)
    health_score, breakdown = (None, [])
    if snap.reachable and not snap.error_code:
        latencies = [r["latency_ms"] for r in recent[:4] if r["latency_ms"] is not None]
        health_score, breakdown = scoring.score(metrics, recent_latencies=latencies,
                                                failed_in_last_10=min(failed, 10))  # fmt: skip
    state = scoring.state_for(reachable=snap.reachable, error_code=snap.error_code,
                              error_stage=snap.error_stage, health_score=health_score)  # fmt: skip
    return _persist(db, row, snap, metrics, state, health_score, breakdown)


def _probe(row: dict[str, Any], secret, settings) -> StatusSnapshot:
    try:
        adapter_cls = get_adapter_class(row["engine"])
    except AdapterUnavailable as exc:
        return _failed(exc.code, exc.message, "ADAPTER")
    try:
        cfg = service.target_config(row, row["ws_settings"], settings)
    except service.TargetUnavailable as exc:
        stage = "TLS" if exc.code == "CONN_TLS_REQUIRED" else "NETWORK_POLICY"
        return _failed(exc.code, exc.message, stage)
    try:
        with pools.target_slot(str(row["id"]), settings.max_target_sessions, wait_s=1.0):
            return adapter_cls(cfg, secret).status()
    except pools.TargetBusy as exc:
        return _failed("TARGET_BUSY", str(exc), "LENS")


def _persist(
    db, row, snap: StatusSnapshot, metrics, state, health_score, breakdown
) -> dict[str, Any]:
    with db.transaction() as conn:
        out = conn.execute(
            """INSERT INTO app.status_snapshots (workspace_id, connection_id, taken_at, state, reachable, latency_ms,
                     error_code, error_message, server_version, uptime_s, db_size_bytes, health_score,
                     score_breakdown, metrics, details, unavailable)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (row["workspace_id"], row["id"], snap.taken_at, state, snap.reachable, snap.latency_ms, snap.error_code,
             snap.error_message, snap.server_version, snap.uptime_s, snap.db_size_bytes, health_score,
             json.dumps([d.as_dict() for d in breakdown]), json.dumps(metrics, default=str),
             json.dumps(snap.details, default=str), json.dumps(snap.unavailable)),
        ).fetchone()  # fmt: skip
        conn.execute(
            """UPDATE app.connections
                  SET last_state_change_at = CASE WHEN current_state <> %(state)s THEN %(at)s
                                                  ELSE last_state_change_at END,
                      current_state = %(state)s, health_score = %(score)s, last_checked_at = %(at)s,
                      server_version = COALESCE(%(version)s, server_version)
                WHERE id = %(id)s AND NOT is_paused""",
            {"state": state, "at": snap.taken_at, "score": health_score, "version": snap.server_version,
             "id": row["id"]},
        )  # fmt: skip
        # Every snapshot is followed by an alert evaluation (§14.2).
        queue.enqueue(conn, "alert_eval", connection_id=row["id"], workspace_id=row["workspace_id"],
                      idempotency_key=f"alert_eval:snapshot:{out['id']}")  # fmt: skip
    return out


@job("health_check", timeout_s=10, max_attempts=1, priority=100)
def health_check_job(ctx: JobContext) -> None:
    connection_id = ctx.job["connection_id"] or ctx.payload.get("connection_id")
    check_connection(ctx.db, connection_id, ctx.settings)


def uptime(rows: list[dict[str, Any]]) -> float | None:
    """FR-23: successful checks / total checks; paused snapshots are excluded."""
    counted = [r for r in rows if r["state"] != "paused"]
    if not counted:
        return None
    ok = sum(1 for r in counted if r["state"] in ("healthy", "degraded"))
    return round(100.0 * ok / len(counted), 2)


def isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
