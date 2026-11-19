# Runbook: invalid schema (schema drift)

**Symptoms:**
* `landing_schema_mismatch` FAILs.
* The loader reports `SchemaMismatchError: expected columns [...] got [...]`.
* Spark reports `DELTA_FAILED_TO_MERGE_FIELDS ... DOUBLE and STRING`.
* Auto Loader rows have `_rescued_data` set.

Past example: **INC-2026-001**. To practise: `python -m rideops.monitoring simulate-incident`.

## Triage
1. Find the rejected files:
   ```sql
   SELECT file_path, error_message, loaded_at FROM ops.ingestion_log WHERE status = 'failed';
   ```
2. Compare the file with the contract (the field lists in `rideops.generators.*`):
   ```bash
   head -2 data/landing/bookings/batch_date=2026-11-01/bookings_20261101_9001.csv
   ```
3. Decide what kind of change it is:
   * **Additive** (a new optional column): can be absorbed with Auto Loader rescue mode or `mergeSchema`.
   * **Breaking** (renamed, removed or retyped column): must not be loaded as-is.

## Resolve
* **Breaking change:** quarantine the file, and ask the source to fix it and re-send. For the practice
  incident, `python -m rideops.monitoring resolve-incident` moves the file to `data/quarantine_files/`
  and clears its failed ingestion record.
* **Agreed, intentional change:** update the contract (field list, a new `sql/ddl` migration, the
  transforms and the tests) in one pull request, deploy it, then re-send or re-load the file.
* Never relax schema enforcement just to make the error go away.

## Verify
After `python -m rideops.db all`, `landing_schema_mismatch` PASSes and the alert auto-resolves.
This exact flow was run end to end on 2026-11-19.

## Prevent
Version source contracts. Ask producers for contract tests. Keep failed files visible until someone handles them.
