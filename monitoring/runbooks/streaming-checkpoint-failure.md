# Runbook: streaming checkpoint failure

**Symptoms:** `StreamingQueryException ... checkpoint ... incompatible` or `offsets log version mismatch`;
the stream fails immediately on start, usually right after a code change.

**Background:** the checkpoint stores which input has been processed (offsets) and the **state** of
stateful operators (windows, deduplication). It is tied to the shape of the query, so changing an
aggregation, a key or the watermark makes the old state unreadable.

## Triage
1. What changed? A code change to a stateful part of the query, a Spark or Databricks Runtime upgrade,
   or a deleted or partly deleted checkpoint folder.
2. Is the sink idempotent? Delta sinks with checkpoints are exactly-once, so rebuilding is safe as long
   as you also rebuild the output.

## Resolve
* **Compatible change** (a filter or a projection): just restart. The stream continues from the checkpoint.
* **Incompatible change:** start a **new checkpoint** and **rebuild the output** from the source.
  Locally, move the old checkpoint and output aside, then re-run:
  ```bash
  mv data/lakehouse/_checkpoints/gold_ride_event_windows data/lakehouse/_checkpoints/gold_ride_event_windows.bak
  mv data/lakehouse/gold/ride_event_windows data/lakehouse/gold/ride_event_windows.bak
  docker compose --profile spark run --rm spark python -m rideops.spark stream
  ```
  On Databricks, use a new checkpoint path and replace or truncate the target table first.
* Never edit checkpoint files by hand.

## Verify
The stream processes all available input, and the window counts for a sample day match a batch recount.

## Prevent
Treat changes to stateful queries as migrations: review them, version the checkpoint path, and plan a
backfill. Keep checkpoints on durable storage (a Unity Catalog volume or S3), not on local disk.
