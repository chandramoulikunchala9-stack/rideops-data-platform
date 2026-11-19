-- Pipeline monitoring (BI job monitoring). Used by the monitoring API in rideops.api.

-- name: pipeline_health_summary
-- Per pipeline: success rate, last run, last success and duration percentiles.
SELECT
    pipeline_name,
    count(*)                                                    AS runs,
    round(100.0 * count(*) FILTER (WHERE status = 'SUCCESS') / count(*), 1) AS success_pct,
    max(start_ts)                                               AS last_run_at,
    max(end_ts) FILTER (WHERE status = 'SUCCESS')               AS last_success_at,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_sec)
        FILTER (WHERE status = 'SUCCESS')                       AS p50_duration_sec,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_sec)
        FILTER (WHERE status = 'SUCCESS')                       AS p95_duration_sec
FROM ops.pipeline_runs
GROUP BY pipeline_name
ORDER BY success_pct, pipeline_name;

-- name: failed_runs_recent
SELECT run_id, pipeline_name, trigger, retry_count, start_ts, error_message
FROM ops.pipeline_runs
WHERE status = 'FAILED'
ORDER BY start_ts DESC
LIMIT 50;

-- name: long_running_runs
-- Successful runs that took more than twice their pipeline's median duration.
WITH baseline AS (
    SELECT pipeline_name,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_sec) AS median_sec
    FROM ops.pipeline_runs
    WHERE status = 'SUCCESS'
    GROUP BY pipeline_name
)
SELECT r.run_id, r.pipeline_name, r.duration_sec, round(b.median_sec::numeric) AS median_sec,
       round((r.duration_sec / nullif(b.median_sec, 0))::numeric, 1) AS times_median  -- percentile_cont returns double
FROM ops.pipeline_runs r
JOIN baseline b USING (pipeline_name)
WHERE r.status = 'SUCCESS' AND r.duration_sec > 2 * b.median_sec
ORDER BY times_median DESC;

-- name: consecutive_failures
-- Current streak of failures per pipeline: runs since its last success (gaps-and-islands).
WITH ordered AS (
    SELECT pipeline_name, status, start_ts,
           count(*) FILTER (WHERE status = 'SUCCESS') OVER (
               PARTITION BY pipeline_name ORDER BY start_ts
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS successes_so_far
    FROM ops.pipeline_runs
),
latest_island AS (
    SELECT pipeline_name, max(successes_so_far) AS island
    FROM ordered
    GROUP BY pipeline_name
)
SELECT o.pipeline_name, count(*) FILTER (WHERE o.status = 'FAILED') AS failures_since_last_success
FROM ordered o
JOIN latest_island l ON l.pipeline_name = o.pipeline_name AND o.successes_so_far = l.island
GROUP BY o.pipeline_name
HAVING count(*) FILTER (WHERE o.status = 'FAILED') > 0;

-- name: daily_run_outcomes
SELECT
    (start_ts AT TIME ZONE 'UTC')::date                 AS run_date,
    count(*) FILTER (WHERE status = 'SUCCESS')          AS succeeded,
    count(*) FILTER (WHERE status = 'FAILED')           AS failed,
    count(*) FILTER (WHERE status = 'CANCELLED')        AS cancelled,
    sum(records_read)                                   AS records_read
FROM ops.pipeline_runs
GROUP BY 1
ORDER BY 1;

-- name: ingestion_by_entity
SELECT entity, count(*) AS files, sum(rows_loaded) AS rows, pg_size_pretty(sum(file_bytes)) AS size,
       max(loaded_at) AS last_loaded_at, count(*) FILTER (WHERE status = 'failed') AS failed_files
FROM ops.ingestion_log
GROUP BY entity
ORDER BY entity;
