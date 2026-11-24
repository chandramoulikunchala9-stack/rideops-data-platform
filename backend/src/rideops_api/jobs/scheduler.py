"""Scheduler: every tick, turn due `app.schedules` rows and periodic system jobs into queued jobs.

Only one scheduler acts per tick: a transaction-scoped advisory lock (ADR-6 leader election) is
taken with pg_try_advisory_xact_lock, so concurrent instances skip instead of double-enqueueing.
Idempotency keys make even a racing duplicate harmless.
"""

import logging
import threading
from dataclasses import dataclass

from rideops_api.db import Database
from rideops_api.jobs import queue, registry

logger = logging.getLogger(__name__)
LEADER_LOCK_ID = 42

# Periodic system jobs: (job type, bucket size in seconds).
SYSTEM_JOBS: list[tuple[str, int]] = [("retention", 3600)]


@dataclass
class TickResult:
    leader: bool
    enqueued: int = 0
    reaped: int = 0


def tick(db: Database) -> TickResult:
    with db.transaction() as conn:
        got = conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s) AS ok", (LEADER_LOCK_ID,)
        ).fetchone()["ok"]
        if not got:
            return TickResult(leader=False)
        result = TickResult(leader=True, reaped=queue.reap_stale(conn))

        due = conn.execute(
            """SELECT s.connection_id, s.job_type, s.interval_s, c.workspace_id,
                      floor(extract(epoch FROM s.next_run_at))::bigint AS bucket
                 FROM app.schedules s
                 JOIN app.connections c ON c.id = s.connection_id
                WHERE s.next_run_at <= now() AND NOT c.is_paused AND c.deleted_at IS NULL
                ORDER BY s.next_run_at LIMIT 500 FOR UPDATE OF s SKIP LOCKED"""
        ).fetchall()
        for row in due:
            if registry.get(row["job_type"]) is None:
                logger.warning("Schedule for unknown job type %s skipped", row["job_type"])
                continue
            key = f"{row['job_type']}:{row['connection_id']}:{row['bucket']}"
            if queue.enqueue(
                conn,
                row["job_type"],
                connection_id=row["connection_id"],
                workspace_id=row["workspace_id"],
                idempotency_key=key,
            ):
                result.enqueued += 1
            conn.execute(
                """UPDATE app.schedules
                      SET next_run_at = GREATEST(next_run_at + make_interval(secs => interval_s), now())
                    WHERE connection_id = %s AND job_type = %s""",
                (row["connection_id"], row["job_type"]),
            )

        for job_type, bucket_s in SYSTEM_JOBS:
            bucket = conn.execute("SELECT floor(extract(epoch FROM now()) / %s)::bigint AS b",
                                  (bucket_s,)).fetchone()["b"]  # fmt: skip
            if queue.enqueue(conn, job_type, idempotency_key=f"{job_type}:{bucket}"):
                result.enqueued += 1
    return result


def run_forever(db: Database, stop: threading.Event, interval_s: float = 15.0) -> None:
    logger.info("Scheduler started (every %.0fs)", interval_s)
    while not stop.is_set():
        try:
            tick(db)
        except Exception:  # noqa: BLE001
            logger.exception("Scheduler tick failed")
        stop.wait(interval_s)
