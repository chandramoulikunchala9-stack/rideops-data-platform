# Runbooks

Step-by-step guides for on-call engineers. Each one covers symptoms, detection, triage, how to resolve,
how to verify, and how to prevent a repeat. Commands assume the repository root and a configured `.env`.

| Runbook | Typical alert / check | Example |
|---|---|---|
| [failed-pipeline.md](failed-pipeline.md) | `failed_pipeline` | Any FAILED run |
| [missing-source-file.md](missing-source-file.md) | `bookings_missing_days`, `FileNotFoundException` | No `batch_date=` folder for a day |
| [invalid-schema.md](invalid-schema.md) | `landing_schema_mismatch`, `DELTA_FAILED_TO_MERGE_FIELDS` | INC-2026-001 |
| [permission-denied.md](permission-denied.md) | `AccessDeniedException`, `PERMISSION_DENIED`, HTTP 403 | IAM or Unity Catalog grant missing |
| [insufficient-compute.md](insufficient-compute.md) | `long_running`, `ExecutorLostFailure` | INC-2026-003 |
| [duplicate-data.md](duplicate-data.md) | `bookings_duplicate_deliveries`, `*_key_unique` | Re-sent files |
| [delayed-ingestion.md](delayed-ingestion.md) | `stale_ingestion`, `ingestion_freshness` | Source late |
| [streaming-checkpoint-failure.md](streaming-checkpoint-failure.md) | `StreamingQueryException` | Incompatible checkpoint |

**Severity guide:**
- **SEV1**: customer-facing data is wrong or unavailable.
- **SEV2**: a pipeline is blocked and dashboards are more than a day stale.
- **SEV3**: degraded or partial.
- **SEV4**: cosmetic or self-healing.

Record every SEV1–3 incident as a file in `monitoring/incidents/`, then run
`python -m rideops.monitoring seed-incidents`.
