import threading
import time

import pytest

from rideops_api import migrate
from rideops_api.jobs import queue, registry, scheduler
from rideops_api.jobs.worker import Worker
from tests.lens.conftest import make_connection, sql

if "test_record" not in registry.REGISTRY:

    @registry.job("test_record", timeout_s=5, max_attempts=1, priority=50)
    def _record(ctx: registry.JobContext) -> None:
        time.sleep(0.002)  # widen the race window
        with ctx.db.pool.connection() as conn:
            conn.execute("INSERT INTO test_job_runs (job_id, worker) VALUES (%s, %s)",
                         (ctx.job["id"], ctx.job["locked_by"]))  # fmt: skip


@pytest.fixture(autouse=True)
def clean_jobs(lens_conninfo):
    sql(lens_conninfo, "CREATE TABLE IF NOT EXISTS test_job_runs (job_id bigint, worker text)")
    sql(lens_conninfo, "TRUNCATE app.jobs, app.schedules, test_job_runs")
    yield


def test_enqueue_is_idempotent(meta_db):
    with meta_db.pool.connection() as conn:
        first = queue.enqueue(conn, "noop", idempotency_key="health:abc:1")
        second = queue.enqueue(conn, "noop", idempotency_key="health:abc:1")
    assert first is not None and second is None
    with pytest.raises(ValueError), meta_db.pool.connection() as conn:
        queue.enqueue(conn, "not_a_job")


def test_each_job_claimed_exactly_once_with_four_concurrent_workers(meta_db, lens_conninfo):
    """Phase 11 acceptance: job claimed exactly once with 4 concurrent workers."""
    n_jobs = 200
    with meta_db.transaction() as conn:
        for i in range(n_jobs):
            queue.enqueue(conn, "test_record", payload={"i": i})
    workers = [Worker(meta_db, worker_id=f"w{i}", job_types=["test_record"]) for i in range(4)]
    barrier = threading.Barrier(4)
    counts: dict[str, int] = {}

    def run(w: Worker) -> None:
        barrier.wait()
        counts[w.worker_id] = w.drain(max_jobs=n_jobs)

    threads = [threading.Thread(target=run, args=(w,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    runs = sql(lens_conninfo, "SELECT job_id, count(*) AS n FROM test_job_runs GROUP BY job_id")
    assert len(runs) == n_jobs and all(r["n"] == 1 for r in runs)
    assert sum(counts.values()) == n_jobs
    assert sum(1 for c in counts.values() if c > 0) >= 2, counts  # work was actually shared
    status = sql(lens_conninfo, "SELECT status, count(*) AS n FROM app.jobs GROUP BY status")
    assert status == [{"status": "done", "n": n_jobs}]


def test_priority_order(meta_db):
    with meta_db.transaction() as conn:
        low = queue.enqueue(conn, "noop", priority=1)
        high = queue.enqueue(conn, "noop", priority=99)
    w = Worker(meta_db)
    assert w.run_once()["id"] == high
    assert w.run_once()["id"] == low
    assert w.run_once() is None


def test_failure_backoff_then_failed(meta_db, lens_conninfo):
    with meta_db.pool.connection() as conn:
        job_id = queue.enqueue(conn, "noop", payload={"fail": True})
        conn.execute("UPDATE app.jobs SET max_attempts = 2 WHERE id = %s", (job_id,))
    w = Worker(meta_db)
    w.run_once()
    row = sql(
        lens_conninfo,
        "SELECT status, attempts, last_error, run_at > now() AS delayed FROM app.jobs WHERE id = %s",
        job_id,
    )[0]
    assert (
        row["status"] == "queued"
        and row["attempts"] == 1
        and row["delayed"]
        and "requested failure" in row["last_error"]
    )
    assert w.run_once() is None  # backoff: not due yet
    sql(lens_conninfo, "UPDATE app.jobs SET run_at = now() WHERE id = %s RETURNING 1", job_id)
    w.run_once()
    row = sql(
        lens_conninfo, "SELECT status, attempts, finished_at FROM app.jobs WHERE id = %s", job_id
    )[0]
    assert row["status"] == "failed" and row["attempts"] == 2 and row["finished_at"] is not None


def test_unknown_job_type_fails_cleanly(meta_db, lens_conninfo):
    sql(
        lens_conninfo,
        "INSERT INTO app.jobs (job_type, max_attempts) VALUES ('ghost', 1) RETURNING 1",
    )
    Worker(meta_db).run_once()
    row = sql(lens_conninfo, "SELECT status, last_error FROM app.jobs WHERE job_type = 'ghost'")[0]
    assert row["status"] == "failed" and "No handler" in row["last_error"]


def test_reaper_requeues_lost_jobs(meta_db, lens_conninfo):
    with meta_db.pool.connection() as conn:
        job_id = queue.enqueue(conn, "noop")
        queue.claim(conn, "crashed-worker")
        conn.execute(
            "UPDATE app.jobs SET heartbeat_at = now() - interval '10 minutes', max_attempts = 2 WHERE id = %s",
            (job_id,),
        )
        assert queue.reap_stale(conn) == 1
    row = sql(
        lens_conninfo, "SELECT status, locked_by, last_error FROM app.jobs WHERE id = %s", job_id
    )[0]
    assert (
        row["status"] == "queued" and row["locked_by"] is None and "heartbeat" in row["last_error"]
    )
    # A late completion from the lost worker cannot overwrite the re-queued job.
    with meta_db.pool.connection() as conn:
        queue.complete(conn, job_id, "crashed-worker")
    assert (
        sql(lens_conninfo, "SELECT status FROM app.jobs WHERE id = %s", job_id)[0]["status"]
        == "queued"
    )


def test_scheduler_enqueues_due_schedules_once(meta_db, lens_conninfo):
    due, later, paused = (
        make_connection(lens_conninfo),
        make_connection(lens_conninfo),
        make_connection(lens_conninfo, paused=True),
    )
    sql(
        lens_conninfo,
        """INSERT INTO app.schedules (connection_id, job_type, interval_s, next_run_at)
           VALUES (%s, 'noop', 300, now() - interval '1 second'),
                  (%s, 'noop', 300, now() + interval '1 hour'),
                  (%s, 'noop', 300, now() - interval '1 second')""",
        due,
        later,
        paused,
    )
    first = scheduler.tick(meta_db)
    # one due schedule + the hourly retention and 15-min dashboard jobs; the paused
    # connection's schedule is skipped (FR-19)
    assert first.leader and first.enqueued == 4  # + the hourly roll-up
    second = scheduler.tick(meta_db)
    assert second.enqueued == 0  # schedule advanced; retention bucket already queued
    jobs = sql(
        lens_conninfo, "SELECT job_type, count(*) AS n FROM app.jobs GROUP BY job_type ORDER BY 1"
    )
    assert jobs == [{"job_type": "dashboards_due", "n": 1}, {"job_type": "noop", "n": 1},
                    {"job_type": "retention", "n": 1}, {"job_type": "rollup", "n": 1}]  # fmt: skip
    nxt = sql(lens_conninfo, "SELECT next_run_at > now() AS future FROM app.schedules WHERE connection_id = %s",
              due)[0]  # fmt: skip
    assert nxt["future"]


def test_only_one_scheduler_leads(meta_db, lens_conninfo):
    with meta_db.transaction() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (scheduler.LEADER_LOCK_ID,))
        assert scheduler.tick(meta_db).leader is False  # another instance holds the lock
    assert scheduler.tick(meta_db).leader is True


def test_retention_job_runs(meta_db, lens_conninfo):
    sql(
        lens_conninfo,
        """INSERT INTO app.login_attempts (email, succeeded, created_at)
                          VALUES ('old@example.com', false, now() - interval '40 days') RETURNING 1""",
    )
    with meta_db.pool.connection() as conn:
        queue.enqueue(conn, "retention")
    Worker(meta_db).run_once()
    assert sql(lens_conninfo, "SELECT status FROM app.jobs")[0]["status"] == "done"
    assert not sql(
        lens_conninfo, "SELECT 1 FROM app.login_attempts WHERE email = 'old@example.com'"
    )


def test_internal_tick_endpoint(client, lens_conninfo):
    assert client.post("/internal/tick").status_code == 403
    assert client.post("/internal/tick", headers={"X-Internal-Token": "wrong"}).status_code == 403
    r = client.post("/internal/tick", headers={"X-Internal-Token": "tick-secret"})
    assert r.status_code == 200
    body = r.json()
    # the hourly retention job and the 15-minute dashboard refresh, both run in the same tick
    assert body["leader"] and body["enqueued"] == 3 and body["jobs_run"] == 3


def test_health_and_readiness(client, lens_conninfo):
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"] == {
        "database": "ok",
        "migrations": "ok",
        "keys": "ok",
    }


def test_migrations_roundtrip(lens_conninfo):
    """Full downgrade → upgrade in a throwaway database, so the shared test DB (and the type OIDs its
    pooled connections have cached) is never touched."""
    import psycopg

    from rideops.config import PostgresSettings

    base = PostgresSettings()
    with psycopg.connect(base.conninfo(), autocommit=True) as admin:
        admin.execute("DROP DATABASE IF EXISTS rideops_lens_migrations WITH (FORCE)")
        admin.execute("CREATE DATABASE rideops_lens_migrations")
    conninfo = base.conninfo("rideops_lens_migrations")
    try:
        migrate.upgrade(conninfo)
        migrate.downgrade(conninfo, "base")
        assert sql(conninfo, "SELECT to_regclass('app.users') IS NULL AS gone")[0]["gone"]
        migrate.upgrade(conninfo)
        version = sql(conninfo, "SELECT version_num FROM app.alembic_version")[0]["version_num"]
        assert version == migrate.head_revision()
    finally:
        with psycopg.connect(base.conninfo(), autocommit=True) as admin:
            admin.execute("DROP DATABASE IF EXISTS rideops_lens_migrations WITH (FORCE)")
