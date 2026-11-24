"""Metric roll-ups (§10.6, F-PL-2): raw status snapshots → hourly → daily aggregates.

Raw snapshots are kept RETENTION_RAW_DAYS (7), hourly roll-ups RETENTION_HOURLY_DAYS (90) and daily
roll-ups RETENTION_DAILY_DAYS (365). Uptime and history read raw rows for recent buckets and roll-ups
for older ones (`checks_by_bucket`), so deleting raw data never changes what the UI shows.

Each roll-up row is (connection, grain, bucket, metric). Metric "__checks__" carries the up/total
check counts; "latency_ms" and "health_score" carry min/max/avg/p95 with `samples`.
"""

from datetime import datetime
from typing import Any

from rideops_api.jobs.registry import JobContext, job
from rideops_api.settings import get_lens_settings

HOURLY = """
INSERT INTO app.metric_rollups (connection_id, grain, bucket, metric, min, max, avg, p95, samples,
                                up_checks, total_checks)
SELECT connection_id, 'hour', date_trunc('hour', taken_at), m.metric,
       min(m.v), max(m.v), avg(m.v), percentile_cont(0.95) WITHIN GROUP (ORDER BY m.v),
       count(m.v),
       count(*) FILTER (WHERE m.metric = '__checks__' AND state IN ('healthy', 'degraded')),
       count(*) FILTER (WHERE m.metric = '__checks__' AND state <> 'paused')
  FROM app.status_snapshots s
  CROSS JOIN LATERAL (VALUES ('latency_ms', s.latency_ms::float8), ('health_score', s.health_score::float8),
                             ('__checks__', NULL::float8)) AS m(metric, v)
 WHERE taken_at >= date_trunc('hour', now() - make_interval(days => %(days)s))
 GROUP BY 1, 2, 3, 4
ON CONFLICT (connection_id, grain, bucket, metric) DO UPDATE SET
  min = EXCLUDED.min, max = EXCLUDED.max, avg = EXCLUDED.avg, p95 = EXCLUDED.p95, samples = EXCLUDED.samples,
  up_checks = EXCLUDED.up_checks, total_checks = EXCLUDED.total_checks
"""

DAILY = """
INSERT INTO app.metric_rollups (connection_id, grain, bucket, metric, min, max, avg, p95, samples,
                                up_checks, total_checks)
SELECT connection_id, 'day', date_trunc('day', bucket), metric, min(min), max(max),
       sum(avg * samples) / nullif(sum(samples), 0), max(p95), sum(samples), sum(up_checks), sum(total_checks)
  FROM app.metric_rollups
 WHERE grain = 'hour' AND bucket >= date_trunc('day', now() - make_interval(days => %(days)s))
 GROUP BY 1, 2, 3, 4
ON CONFLICT (connection_id, grain, bucket, metric) DO UPDATE SET
  min = EXCLUDED.min, max = EXCLUDED.max, avg = EXCLUDED.avg, p95 = EXCLUDED.p95, samples = EXCLUDED.samples,
  up_checks = EXCLUDED.up_checks, total_checks = EXCLUDED.total_checks
"""


def rollup(db: Any, settings=None) -> dict[str, int]:
    """Recompute every hour still covered by raw data, then every day still covered by hours.
    Idempotent: re-running only refreshes the same rows."""
    settings = settings or get_lens_settings()
    with db.transaction() as conn:
        hours = conn.execute(HOURLY, {"days": settings.retention_raw_days}).rowcount
        days = conn.execute(DAILY, {"days": settings.retention_hourly_days}).rowcount
    return {"hour_rows": hours, "day_rows": days}


@job("rollup", timeout_s=300, max_attempts=3, priority=10)
def rollup_job(ctx: JobContext) -> None:
    rollup(ctx.db, ctx.settings)


def checks_by_bucket(conn, connection_id: Any, grain: str, start: datetime, end: datetime,
                     raw_days: int) -> list[dict[str, Any]]:  # fmt: skip
    """Per-bucket uptime + averages: raw snapshots for every hour that still has them, hourly roll-ups
    before that. Retention deletes raw rows on whole-hour boundaries, so no hour is half raw."""
    assert grain in ("hour", "day")
    return conn.execute(
        """WITH cutoff AS (  -- raw rows wherever they still exist; roll-ups only for hours already purged
             SELECT coalesce(date_trunc('hour', min(taken_at)), now()) AS t
               FROM app.status_snapshots WHERE connection_id = %(c)s),
           raw AS (
             SELECT date_trunc(%(g)s, taken_at) AS at, sum(latency_ms) AS lat_sum, count(latency_ms) AS lat_n,
                    sum(health_score) AS score_sum, count(health_score) AS score_n,
                    count(*) FILTER (WHERE state IN ('healthy', 'degraded')) AS up,
                    count(*) FILTER (WHERE state <> 'paused') AS total
               FROM app.status_snapshots, cutoff
              WHERE connection_id = %(c)s AND taken_at >= greatest(%(s)s, cutoff.t) AND taken_at < %(e)s
              GROUP BY 1),
           old AS (
             SELECT date_trunc(%(g)s, bucket) AS at,
                    sum(avg * samples) FILTER (WHERE metric = 'latency_ms') AS lat_sum,
                    sum(samples) FILTER (WHERE metric = 'latency_ms') AS lat_n,
                    sum(avg * samples) FILTER (WHERE metric = 'health_score') AS score_sum,
                    sum(samples) FILTER (WHERE metric = 'health_score') AS score_n,
                    sum(up_checks) FILTER (WHERE metric = '__checks__') AS up,
                    sum(total_checks) FILTER (WHERE metric = '__checks__') AS total
               FROM app.metric_rollups, cutoff
              WHERE connection_id = %(c)s AND grain = 'hour' AND bucket >= %(s)s AND bucket < least(%(e)s, cutoff.t)
              GROUP BY 1)
           SELECT at, sum(lat_sum) / nullif(sum(lat_n), 0) AS latency_ms,
                  sum(score_sum) / nullif(sum(score_n), 0) AS health_score,
                  coalesce(sum(up), 0)::int AS up_checks, coalesce(sum(total), 0)::int AS total_checks
             FROM (SELECT * FROM raw UNION ALL SELECT * FROM old) x
            GROUP BY at ORDER BY at""",
        {"c": connection_id, "g": grain, "s": start, "e": end, "raw": raw_days},
    ).fetchall()
