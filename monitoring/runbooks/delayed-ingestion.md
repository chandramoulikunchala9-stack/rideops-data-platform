# Runbook: delayed ingestion / stale data

**Symptoms:** a `stale_ingestion` alert ("No new source files loaded for N hours"), `ingestion_freshness`
FAILs, and dashboards still show yesterday's numbers.

## Triage
1. Is the source late, or is our pipeline stuck?
   ```sql
   SELECT entity, max(loaded_at) FROM ops.ingestion_log WHERE status = 'loaded' GROUP BY entity;
   SELECT pipeline_name, status, start_ts FROM ops.pipeline_runs WHERE status = 'RUNNING';
   ```
2. **Files are in landing but not loaded:** the scheduler did not run, a run is hanging (check for a
   `long_running` alert), or the database is unreachable.
3. **No files in landing:** the source is late. Contact them and check their status page.

## Resolve
* **Stuck run:** cancel it, find the cause (see [insufficient-compute](insufficient-compute.md)), and re-run.
* **Scheduler:** re-enable or unpause the job (`schedule_pause_status` in the bundle), or run it manually.
* **Late source:** tell dashboard users about the expected delay, and re-run once the files land.

## Verify
After `python -m rideops.db all`, `ingestion_freshness` PASSes and the alert auto-resolves.

## Prevent
Agree a freshness SLA per source, set a `timeout_seconds` on every job, and enable duration-warning
notifications (the bundle's `health` rules).
