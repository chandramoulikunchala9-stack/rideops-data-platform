# RideOps Architecture

This document describes the target architecture and the decisions behind it.
Implementation status for each component lives in [../PROJECT_STATUS.md](../PROJECT_STATUS.md).

## 1. Design goals

1. **Runs locally for free.** Every core feature works on a laptop with Python, Java, Node and Docker.
2. **Cloud is optional and honest.** AWS S3, AWS billing data and Databricks are opt-in. Code that has only been
   written for the cloud, and never run there, is labeled that way.
3. **One source of truth per concern.** Business data lives in the lakehouse; the API reads curated copies from PostgreSQL;
   operational metadata (runs, data-quality results, costs) lives in a dedicated `ops` schema.
4. **Testable by design.** Transformations are plain functions that take and return DataFrames, so they can be unit-tested
   without a cluster.

## 2. Layers and data flow

| Step | Component | What it does | Runs locally? |
|---|---|---|---|
| 1 | Generator (`src/rideops/generators`) | Creates reproducible synthetic customers, drivers, vehicles, bookings, payments, events, cost and run records, with deliberate defects (nulls, duplicates, late events) | Yes |
| 2 | Landing zone (`data/landing` or `s3://…-raw`) | Raw CSV / JSON files, partitioned by source and ingestion date | Yes / optional S3 |
| 3 | Ingestion (`src/rideops/ingestion`) | CSV, JSON, REST and incremental file ingestion, with retries, logging and ingestion metadata | Yes |
| 4 | Bronze | Raw records stored as Delta, plus `_ingested_at`, `_source_file` and `_batch_id` | Yes |
| 5 | Silver | Deduplicated, typed, validated records; rejected rows go to a quarantine table with a reason | Yes |
| 6 | Gold | Business aggregates: daily bookings, revenue by city, driver performance, cancellations, payments, vehicle utilization | Yes |
| 7 | Streaming | Ride events using Structured Streaming with event-time windows, watermarks and checkpoints; Auto Loader in Databricks | Local streaming: yes. Auto Loader: workspace only |
| 8 | Serving (PostgreSQL `analytics`) | Gold tables loaded into Postgres for fast API queries | Yes |
| 9 | Ops metadata (PostgreSQL `ops`) | Pipeline runs, logs, data-quality results, incidents, cost and usage | Yes |
| 10 | API (`backend/`) | FastAPI REST endpoints under `/api/v1` | Yes |
| 11 | Dashboard (`frontend/`) | React + TypeScript; all metrics come from the API | Yes |

## 3. Key decisions

### ADR-001: Separate Git repository for the project
The parent `Desktop` folder is itself a Git repository. RideOps has its own repository inside
`rideops-data-platform/` so that project history never mixes with unrelated personal files.

### ADR-002: Delta Lake locally through `delta-spark`
Using the open-source Delta Lake library lets us demonstrate ACID writes, `MERGE`, schema enforcement and evolution,
time travel, and `OPTIMIZE` locally, with the same table format Databricks uses.

### ADR-003: PostgreSQL as the serving and metadata store
The API should not start a Spark session for each request. Gold tables are small aggregates, so copying them into
PostgreSQL gives fast, indexable queries and lets us demonstrate SQL optimization (indexes, `EXPLAIN ANALYZE`).
The same database holds operational metadata, because that is transactional, row-level data.

### ADR-004: Run Spark in Docker on Windows (recommended, to confirm in Phase 3)
Native Spark on Windows needs Hadoop helper binaries (`winutils.exe`, `hadoop.dll`) to write files, and is a common
source of setup failures. A small Docker image containing Python and Java avoids this and matches Linux CI.
Native Windows Spark stays as a fallback, since JDK 21 is already installed.

### ADR-005: Retry endpoint runs a real local job or is disabled
`POST /api/v1/pipelines/{id}/retry` will start a whitelisted local pipeline command and record a new run.
If no runner is configured (`RETRY_RUNNER_ENABLED=false`), it returns `409 Conflict` with an explanation
instead of pretending to succeed.

### ADR-006: Simulated cost data is labeled at the row level
Every cost record carries a `source` column (`simulated`, `aws_cur`, `databricks_system_tables`).
The API and UI show this label, so sample numbers are never mistaken for real spending.

## 4. Environments

| Environment | Storage | Compute | Database | Purpose |
|---|---|---|---|---|
| `local` | `./data` | Local Spark (Docker) | Postgres in Docker | Development and demos |
| `dev` (optional) | S3 buckets from Terraform | Databricks workspace | Hosted Postgres | Cloud verification |
| `prod` (documented only) | S3 | Databricks Jobs | Managed Postgres | Shows how targets would be promoted |

## 5. Security boundaries (summary)

- No credentials in source code. Configuration comes from environment variables; AWS access uses profiles or IAM roles.
- Least-privilege IAM: separate read/write permissions per bucket prefix.
- Unity Catalog grants model four roles: Data Engineer, Data Analyst, Platform Administrator, Read-only User.

Details go in `docs/security.md` (Phase 6).
