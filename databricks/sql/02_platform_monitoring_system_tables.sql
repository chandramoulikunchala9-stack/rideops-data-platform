-- Databricks SQL over SYSTEM TABLES: real job, query and billing telemetry from the workspace.
-- These return ACTUAL Databricks usage (unlike the simulated rows in ops.cloud_costs).
-- Requires the system schemas to be enabled by an account admin, and SELECT on system.*.
-- STATUS: written for Databricks SQL; cloud verification pending.

-- name: job_runs_last_7_days
-- BI job monitoring: outcome and duration of every RideOps job run.
SELECT
  j.name                                          AS job_name,
  t.run_id,
  t.result_state,
  t.period_start_time,
  t.period_end_time,
  timestampdiff(SECOND, t.period_start_time, t.period_end_time) AS duration_sec
FROM system.lakeflow.job_run_timeline t
JOIN system.lakeflow.jobs j
  ON j.workspace_id = t.workspace_id AND j.job_id = t.job_id
WHERE j.name LIKE 'rideops-%'
  AND t.period_start_time >= current_timestamp() - INTERVAL 7 DAYS
ORDER BY t.period_start_time DESC;

-- name: failed_job_runs
SELECT j.name, t.run_id, t.result_state, t.termination_code, t.period_start_time
FROM system.lakeflow.job_run_timeline t
JOIN system.lakeflow.jobs j ON j.workspace_id = t.workspace_id AND j.job_id = t.job_id
WHERE j.name LIKE 'rideops-%' AND t.result_state IN ('FAILED', 'ERROR', 'TIMED_OUT')
ORDER BY t.period_start_time DESC;

-- name: slow_bi_queries
-- BI query monitoring: the slowest dashboard queries on SQL warehouses (query history).
SELECT
  executed_by,
  compute.warehouse_id                         AS warehouse_id,
  total_duration_ms / 1000                     AS duration_sec,
  read_bytes / 1024 / 1024                     AS read_mb,
  produced_rows,
  left(statement_text, 200)                    AS statement_preview,
  start_time
FROM system.query.history
WHERE start_time >= current_timestamp() - INTERVAL 1 DAY
  AND statement_text ILIKE '%rideops_%gold%'
ORDER BY total_duration_ms DESC
LIMIT 20;

-- name: daily_cost_by_job
-- Cost reporting: DBUs from system.billing.usage priced with list prices, per RideOps job.
SELECT
  u.usage_date,
  coalesce(u.usage_metadata.job_id, 'non-job')            AS job_id,
  u.sku_name,
  sum(u.usage_quantity)                                   AS dbus,
  round(sum(u.usage_quantity * p.pricing.default), 2)     AS list_cost_usd
FROM system.billing.usage u
JOIN system.billing.list_prices p
  ON  p.sku_name = u.sku_name
  AND u.usage_end_time >= p.price_start_time
  AND (p.price_end_time IS NULL OR u.usage_end_time < p.price_end_time)
WHERE u.custom_tags['project'] = 'rideops'
  AND u.usage_date >= current_date() - INTERVAL 30 DAYS
GROUP BY ALL
ORDER BY u.usage_date DESC, list_cost_usd DESC;

-- name: untagged_spend
-- FinOps: DBUs with no `project` tag cannot be charged back to a team.
SELECT usage_date, sku_name, sum(usage_quantity) AS dbus
FROM system.billing.usage
WHERE custom_tags['project'] IS NULL AND usage_date >= current_date() - INTERVAL 30 DAYS
GROUP BY ALL
ORDER BY dbus DESC;
