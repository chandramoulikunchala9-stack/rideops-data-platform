# Runbook: missing source file

**Symptoms:** `FileNotFoundException: No files found under landing/<entity>/batch_date=...`, the
`bookings_missing_days` check FAILs, and the dashboard shows a gap for one day.

## Triage
1. Confirm what arrived:
   ```bash
   ls data/landing/<entity>/                          # local
   python -m rideops.storage ls landing/<entity>/     # S3
   ```
   ```sql
   SELECT batch_id, file_path, status, loaded_at
   FROM ops.ingestion_log WHERE entity = 'bookings' ORDER BY loaded_at DESC LIMIT 10;
   ```
2. Is it late, or was it never sent? Ask the source team or check the provider status page.
3. Is it in the wrong place? A typo in the partition (`batch_date=2026-10-1`) or the extension
   (`.json` vs `.jsonl`) means the loader's file pattern never matches.

## Resolve
* **Late:** wait, or re-run after delivery. Incremental ingestion picks the file up whenever it lands.
* **Wrong path or name:** move it to the agreed layout
  `landing/<entity>/batch_date=YYYY-MM-DD/<entity>_<YYYYMMDD>_<batch>.<ext>`, then re-run.
* **Lost:** ask the source to re-send it. Never hand-create data. If the business needs the day today,
  publish with a note that the day is incomplete.

## Verify
* `ops.ingestion_log` shows the file as `loaded`.
* `bookings_missing_days` PASSes.
* Gold for that date is rebuilt. The CDF-driven gold job recomputes only the dates that changed.

## Prevent
Agree a delivery SLA with each source, and keep the `stale_ingestion` threshold (26 h) aligned with it.
