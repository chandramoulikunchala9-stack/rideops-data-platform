# Runbook: failed pipeline

**Symptoms:** a `failed_pipeline` alert (CRITICAL), a red row on the Pipelines page, downstream runs CANCELLED.

## Triage (5 minutes)
1. Open the run: dashboard → Pipelines → pipeline → failed run, or `GET /api/v1/runs/<run_id>`.
   Read `error_message` and the logs.
2. Classify the error. The classification tells you which runbook to follow:

   | Error contains | Go to |
   |---|---|
   | `FileNotFound`, `No files found` | [missing-source-file](missing-source-file.md) |
   | `Schema mismatch`, `FAILED_TO_MERGE` | [invalid-schema](invalid-schema.md) |
   | `AccessDenied`, `PERMISSION_DENIED`, 403 | [permission-denied](permission-denied.md) |
   | `ExecutorLost`, `OutOfMemory`, `exceeding memory` | [insufficient-compute](insufficient-compute.md) |
   | `DataQualityException`, duplicate | [duplicate-data](duplicate-data.md) |
   | `TimeoutException`, upstream not available | [delayed-ingestion](delayed-ingestion.md) |
   | `StreamingQueryException`, checkpoint | [streaming-checkpoint-failure](streaming-checkpoint-failure.md) |
   | database connection errors | check `docker compose ps` or the provider status page, then retry |

3. Did the automatic retries already run (rows with `trigger = retry`)? Three identical failures mean the
   error is **not transient**, and another retry will not help.

```sql
SELECT run_id, trigger, retry_count, status, start_ts, left(error_message, 120)
FROM ops.pipeline_runs
WHERE pipeline_name = 'ingest_payments'
ORDER BY start_ts DESC
LIMIT 10;
```

## Resolve
Fix the cause using the specific runbook, then re-run:
* Local SQL pipelines: use the Retry button on the dashboard, or `python -m rideops.db <load|transform|quality>`.
* Lakehouse: `docker compose --profile spark run --rm spark python -m rideops.spark <step>`.
  Checkpoints make re-runs safe: only unprocessed data is read.
* Databricks: *Repair run* re-runs only the failed task and its dependants.

## Verify
* The new run shows SUCCESS.
* Downstream pipelines ran.
* `python -m rideops.monitoring alerts` reports the alert as resolved.

## Prevent
Write an incident file for SEV1–3. Add a data-quality check or test that would have caught the problem earlier.
