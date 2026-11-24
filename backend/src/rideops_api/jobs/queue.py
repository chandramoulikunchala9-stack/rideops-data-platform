"""PostgreSQL job queue (ADR-2): FOR UPDATE SKIP LOCKED claims, idempotency keys,
exponential backoff, heartbeats and a reaper for jobs whose worker died."""

import json
from datetime import datetime
from typing import Any

import psycopg

from rideops_api.jobs import registry

BACKOFF_BASE_S = 5  # 5 s, 10 s, 20 s, ... capped below
BACKOFF_MAX_S = 900


def enqueue(
    conn: psycopg.Connection,
    job_type: str,
    *,
    payload: dict[str, Any] | None = None,
    workspace_id: Any = None,
    connection_id: Any = None,
    idempotency_key: str | None = None,
    run_at: datetime | None = None,
    priority: int | None = None,
) -> int | None:
    """Insert a job; returns its id, or None when the idempotency key already exists."""
    spec = registry.get(job_type)
    if spec is None:
        raise ValueError(f"Unknown job type {job_type!r}")
    row = conn.execute(
        """INSERT INTO app.jobs (job_type, payload, workspace_id, connection_id, idempotency_key,
                                 run_at, priority, max_attempts, timeout_s)
           VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), %s, %s, %s)
           ON CONFLICT (idempotency_key) DO NOTHING
           RETURNING id""",
        (job_type, json.dumps(payload or {}), workspace_id, connection_id, idempotency_key, run_at,
         spec.priority if priority is None else priority, spec.max_attempts, spec.timeout_s),
    ).fetchone()  # fmt: skip
    return row["id"] if row else None


def claim(conn: psycopg.Connection, worker_id: str, job_types: list[str] | None = None) -> dict | None:
    """Atomically take the most urgent due job. Concurrent workers never get the same job."""
    return conn.execute(
        """UPDATE app.jobs
              SET status = 'running', locked_by = %(w)s, locked_at = now(), heartbeat_at = now(),
                  attempts = attempts + 1
            WHERE id = (
                SELECT id FROM app.jobs
                 WHERE status = 'queued' AND run_at <= now()
                   AND (%(types)s::text[] IS NULL OR job_type = ANY(%(types)s))
                 ORDER BY priority DESC, run_at
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1)
        RETURNING *""",
        {"w": worker_id, "types": job_types},
    ).fetchone()


def heartbeat(conn: psycopg.Connection, job_id: int, worker_id: str) -> None:
    conn.execute("UPDATE app.jobs SET heartbeat_at = now() WHERE id = %s AND locked_by = %s AND status = 'running'",
                 (job_id, worker_id))  # fmt: skip


def complete(conn: psycopg.Connection, job_id: int, worker_id: str) -> None:
    conn.execute(
        """UPDATE app.jobs SET status = 'done', finished_at = now(), last_error = NULL
            WHERE id = %s AND locked_by = %s AND status = 'running'""",
        (job_id, worker_id),
    )


def fail(conn: psycopg.Connection, job_id: int, worker_id: str, error: str) -> str:
    """Re-queue with backoff while attempts remain, else mark failed. Returns the new status."""
    row = conn.execute(
        """UPDATE app.jobs
              SET status = CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
                  run_at = CASE WHEN attempts < max_attempts
                                THEN now() + make_interval(secs => LEAST(%s * power(2, attempts - 1), %s))
                                ELSE run_at END,
                  finished_at = CASE WHEN attempts < max_attempts THEN NULL ELSE now() END,
                  locked_by = NULL, locked_at = NULL, heartbeat_at = NULL, last_error = left(%s, 2000)
            WHERE id = %s AND locked_by = %s AND status = 'running'
        RETURNING status""",
        (BACKOFF_BASE_S, BACKOFF_MAX_S, error, job_id, worker_id),
    ).fetchone()
    return row["status"] if row else "lost"


def reap_stale(conn: psycopg.Connection) -> int:
    """Re-queue running jobs whose heartbeat is older than max(2 x timeout, 60 s): their worker
    crashed or was stopped. Jobs out of attempts are failed instead."""
    rows = conn.execute(
        """UPDATE app.jobs
              SET status = CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
                  finished_at = CASE WHEN attempts < max_attempts THEN NULL ELSE now() END,
                  locked_by = NULL, locked_at = NULL, heartbeat_at = NULL,
                  last_error = 'worker lost (heartbeat timeout)'
            WHERE status = 'running'
              AND heartbeat_at < now() - make_interval(secs => GREATEST(2 * timeout_s, 60))
        RETURNING id""",
    ).fetchall()
    return len(rows)
