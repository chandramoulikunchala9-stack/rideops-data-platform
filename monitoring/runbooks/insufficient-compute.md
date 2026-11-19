# Runbook: insufficient compute or a slow job

**Symptoms:**
* `ExecutorLostFailure ... exceeding memory limits` or `OutOfMemoryError`.
* A `long_running` alert (the run is more than 3× its median duration).
* The Spark UI shows spill to disk, or one straggler task.

Past example: **INC-2026-003**.

## Triage
1. Is the input unusually large? Compare `records_read` with recent runs:
   ```sql
   SELECT start_ts::date, records_read, duration_sec FROM ops.pipeline_runs
   WHERE pipeline_name = 'stream_ride_events' ORDER BY start_ts DESC LIMIT 14;
   ```
2. In the Spark UI, under Stages, look for:
   * **skew**: one task takes 10× longer than the rest;
   * **spill** to disk;
   * too many tiny tasks (`spark.sql.shuffle.partitions` set too high);
   * one huge micro-batch after a backlog.

## Resolve
* **Backlog:** cap the batch size (`maxFilesPerTrigger`, or `cloudFiles.maxFilesPerTrigger`) so it drains
  in several bounded batches.
* **Memory:** raise driver or executor memory, or let the job cluster autoscale (1–3 workers).
* **Skew:** use AQE skew-join handling (on by default in Databricks), salt hot keys, or broadcast the smaller side.
* **Locally:** close other apps. Spark needs headroom (`SPARK_DRIVER_MEMORY`). This project's 8 GB laptop
  ran the full dataset, but slowly.

## Verify
The duration is back near the median, the Spark UI shows no spill, and the `long_running` alert has resolved.

## Prevent
Use autoscaling with a maximum (set by the cluster policy) and bounded micro-batches. Watch `cost_per_run`
for jobs that slowly drift upward.
