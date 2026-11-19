# Runbook: duplicate data

**Symptoms:** `bookings_duplicate_deliveries` rises above its threshold, a `*_key_unique` check FAILs
(duplicates reached silver or core), or revenue is suddenly higher than expected.

## Triage
1. Where are the duplicates?
   * **Only in raw or bronze:** this is expected. Sources re-send records, and silver keeps the latest version.
   * **In core or silver:** this is a bug. The dedup key or the MERGE condition is wrong.
   ```sql
   SELECT booking_id, count(*) FROM core.bookings GROUP BY 1 HAVING count(*) > 1;   -- must return no rows
   SELECT booking_id, count(*) AS versions FROM raw.bookings
   GROUP BY 1 HAVING count(*) > 1 ORDER BY 2 DESC LIMIT 20;
   ```
2. Was the same file loaded twice under two names? Look for two `ops.ingestion_log` rows with the same `file_sha256`.

## Resolve
* **Re-delivered file under a new name:** remove its rows from raw (filter by `_source_file`) and re-run
  the transform. MERGE with "latest `updated_at` wins" gives the correct result either way.
* **Duplicates in core or silver:** fix the key or condition, then rebuild the affected tables. With Delta,
  `RESTORE TABLE ... TO VERSION AS OF <n>` back to before the bad run, then re-process.

## Verify
`bookings_primary_key_unique` PASSes, and gold totals still reconcile with silver (`lakehouse_sql_reconciliation`).

## Prevent
Primary keys in core, unique-key expectations in silver, and an ingestion check that rejects a second file
with an identical checksum.
