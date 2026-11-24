# Runbook: Lens metadata database near its size limit

**Symptoms:** Neon (or your Postgres) warns about storage; inserts fail with "disk full" or quota
errors; jobs fail with database errors.

## Triage
```sql
SELECT relname, pg_size_pretty(pg_total_relation_size(c.oid)) AS size
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'app' AND c.relkind = 'r'
 ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 10;
```
The usual suspects are `status_snapshots`, `jobs`, `query_runs`, `table_stats_snapshots` and `audit_log`.

## Resolve
1. Make sure the `retention` and `rollup` jobs run (they're scheduled hourly): check
   `SELECT job_type, status, max(finished_at) FROM app.jobs WHERE job_type IN ('retention','rollup') GROUP BY 1, 2;`
2. Run them now: enqueue via `/internal/tick` or `python -m rideops_api rollup`.
3. Shorten the windows if needed: `RETENTION_RAW_DAYS` (default 7), `RETENTION_HOURLY_DAYS` (90),
   `RETENTION_DAILY_DAYS` (365); lower check frequency for noisy connections.
4. After large deletes, `VACUUM (ANALYZE)` the biggest tables so space is reused.

## Verify
Table sizes stop growing between retention runs; `/readyz` reports the database `ok`.
