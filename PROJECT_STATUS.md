# RideOps — Project Status

**Single source of truth for progress.** Updated at the end of every milestone.
Nothing is marked done unless it was built **and** verified as described in the "Verification" column.

Last updated: 2026-11-18 · Current phase: **Phases 3–5 complete → Phase 6 (AWS, Terraform, costs) next**

---

## 1. Milestone checklist

| Phase | Milestone | Status |
|---|---|---|
| 0 | Repository, architecture, project structure | ✅ Done |
| 1 | Python package, config, logging, synthetic data generators, tests | ✅ Done |
| 2 | PostgreSQL (Docker), schemas, data load, analytical SQL, optimization | ✅ Done |
| 3 | Local Spark, Bronze/Silver/Gold with Delta Lake, data-quality checks, transformation tests | ✅ Done |
| 4 | Databricks notebooks, SQL, Jobs, Unity Catalog, Auto Loader, Streaming, DLT, Asset Bundles | ✅ Written + statically validated (workspace deploy pending) |
| 5 | FastAPI backend, monitoring, data-quality APIs, retry handling | ✅ Done |
| 6 | AWS S3 integration, IAM, cost analytics, Terraform | ⏳ Next |
| 7 | React + TypeScript dashboard | ⬜ Not started |
| 8 | GitHub Actions CI | ⬜ Not started |
| 9 | Hosted deployment and verification | ⬜ Not started |
| 10 | Interview preparation guide | ⬜ Not started |

## 2. Phase 0 — completed work

- [x] Inspected working directory: `rideops-data-platform/` was empty.
- [x] Found that the parent `C:\Users\chandu\Desktop` is a Git repository (no commits, personal files untracked).
      Left it untouched; created a **separate** repository in `rideops-data-platform/` (branch `main`).
- [x] Inventoried tools (table below).
- [x] Created folder structure, `README.md`, `docs/architecture.md`, `CONTRIBUTING.md`, PR template,
      `.gitignore`, `.gitattributes`, `.editorconfig`, `.env.example`.
- [x] First commit made by the user (`9d98ce9`).

**Tests:** none (no code). **Verification:** `git rev-parse --show-toplevel` returns the project folder;
`git check-ignore` confirmed `.env` and `data/*` are ignored.

## 2b. Phase 1 — milestone 1a (foundation + dimension generators) — done

- [x] `pyproject.toml` with pinned dependencies (faker 40.54.0, pydantic-settings 2.15.0; dev: pytest 9.2.0, ruff 0.17.5).
- [x] Virtual environment `.venv` on Python 3.10.6 (3.12 upgrade still recommended, see D1).
- [x] `rideops.config` — typed settings from `RIDEOPS_*` env vars / `.env`, with safe defaults.
- [x] `rideops.logging_config` — one log format for the whole project.
- [x] `rideops.generators` — vehicles (JSONL), drivers (CSV), customers (CSV); scale presets `small` / `full` / `large`;
      per-entity seeded randomness; exact-count defect injection (missing, invalid, duplicate with later `updated_at`,
      legacy timestamp format, orphan foreign key); atomic file writes; Hive-style `batch_date=` partitions; run manifest.
- [x] CLI: `python -m rideops.generators --scale full --seed 42`.

**Tests (run 2026-11-18):** `pytest` → 23 passed (unit: config, defects, entities, writers; integration: CLI incl. full-scale volume test).
`ruff check` and `ruff format --check` → clean.
**Verification:** two full-scale runs with seed 42 produced byte-identical data files (`diff -rq`).
Full scale: 10,000 customers (+100 duplicates), 2,000 drivers (+20), 1,000 vehicles (+10) in ~1 s.

## 2c. Phase 1 — remaining

- [x] 1b: bookings (100,000 at `full`) — life cycle requested→accepted→pickup→dropoff, statuses completed / cancelled /
      no_driver_found / in_progress, IST rush-hour demand, weekend uplift and growth trend, surge pricing, fare formula,
      referential + temporal integrity (no booking before signup / driver join). Payments derived from clean bookings:
      failed attempts with retries (max 3), late settlements (1–3 days), refunds. Separate defect RNG.
      Tests: 34 passed. Full scale: 101,000 booking rows, 86,905 payment rows, ~10 s
      (profiled; timestamp formatting optimised 15 s → 10 s with byte-identical output).
- [x] 1c: ride events (372,918 at `full`) and app events (355,970) with event_ts vs ingest_ts and ~2–3% late arrivals,
      partitioned by ingest date (92–93 daily files); pipeline-run history (938 runs, labelled `run_source=simulated_history`)
      following a dependency graph, with retries, CANCELLED downstream runs, one RUNNING run, and a deliberate incident
      (2026-10-19 `ingest_payments` schema-merge failure); simulated cloud costs (2,240 rows, `source=simulated`) derived
      from run durations, with a runaway-dev-cluster anomaly and untagged-cost defects.
      Runner now streams batches (write-then-free). orjson for JSONL; full run ~26 s (was 98 s before profiling).

**Phase 1 tests (2026-11-18):** `pytest` → 46 passed; `ruff check` / `ruff format --check` clean.

## 2d. Phase 2 — PostgreSQL and SQL — done

- [x] `docker-compose.yml`: PostgreSQL 17.5 (pinned), healthcheck, named volume; password required from `.env` (no default).
- [x] Migrations `sql/ddl/001–040` applied by `rideops.db.migrate` (tracked with checksums in `ops.schema_migrations`);
      reference tables seeded from the Python reference module.
- [x] Schemas: `raw` (text/jsonb + load metadata), `core` (typed, FK/CHECK constraints, `dq_flags`, quarantine table),
      `analytics` (6 gold tables), `ops` (ingestion log, watermarks, pipeline runs + logs, DQ results, costs, incidents, alerts).
- [x] Incremental file ingestion (`rideops.ingestion.postgres_loader`): COPY, schema-contract validation, sha256 + size,
      data and ingestion log committed atomically, retries with backoff for transient errors, failed files recorded.
- [x] SQL ELT `sql/transform/`: watermark-based incremental raw→core with ROW_NUMBER versioning, quarantine, MERGE upserts;
      full-refresh gold build in one transaction.
- [x] 17 data-quality checks in one SQL file (`sql/quality/checks.sql`) → `ops.data_quality_results` (PASS/WARN/FAIL).
- [x] Real pipeline runs recorded by `rideops.monitoring.tracker.RunTracker` (`run_source = local_run`) with logs.
- [x] 45 named analytical queries in 9 scripts (`sql/analytics/01–09`): joins (inner/left/right), CTEs, subqueries,
      HAVING, ROW_NUMBER/RANK/DENSE_RANK/LAG/LEAD/FIRST_VALUE, percentiles, cohorts, gaps-and-islands, z-score anomalies.
- [x] `docs/sql-optimization.md` with real EXPLAIN ANALYZE results (≈48×, 16×, 128× speed-ups).
- [x] CLI: `python -m rideops.db migrate|load|transform|quality|all`.

**Results on `full` data (2026-11-18):** load 204,213 raw rows in 2 s; transform 22 s; 99,187 bookings in core,
813 quarantined (every distinct booking accounted for); re-runs load 0 files / transform 0 rows.
**Tests:** `pytest -m "not slow"` → 66 passed (incl. 9 PostgreSQL end-to-end tests on a throwaway `rideops_test` DB).
**Bug found and fixed during verification:** the `bookings_reject_reasons` check counted reasons instead of rows
(reported 4 instead of 813); caught by reconciling against an independent query.

## 2e. Phases 3–5 — done (2026-11-19)

**Phase 3 — Spark + Delta Lake** (`src/rideops/spark/`, runs in `docker/spark` image: PySpark 4.2.0, delta-spark 4.4.1, Java 17)
- Bronze: file streams with `availableNow` + checkpoints (exactly-once, incremental), all-string schemas, `_corrupt_record`.
- Silver: per-entity pure transforms mirroring the SQL rules, quarantine table, `foreachBatch` MERGE upserts.
- Gold: Change-Data-Feed-driven incremental daily tables (`replaceWhere`), full rebuild of small tables, app funnel.
- Streaming: ride events with event-time watermark, `dropDuplicatesWithinWatermark`, 15-min windows, late-drop metrics.
- Delta tour: ACID, schema enforcement/evolution, MERGE, time travel, history, OPTIMIZE ZORDER, VACUUM.
- Spark DQ expectations (stored in `ops.data_quality_results`), gold export to PostgreSQL schema `lakehouse`,
  SQL↔Spark reconciliation check (`ops.reconcile_lakehouse_daily()`).
- **Tests:** `docker compose run --rm spark pytest tests/spark` → 15 passed (10 min on this 8 GB laptop).
- Findings fixed during verification: import-time Column objects; Delta `count()` answered from log stats hid a
  VACUUM effect; multi-stateful-operator late-event semantics (previous-batch watermark) — test corrected.

**Phase 4 — Databricks** (`databricks/`): Asset Bundle (dev/prod, wheel artifact, run_as SP), 3 Lakeflow Jobs
(DQ-gated DAG, 15-min stream, backfill), DLT pipeline (expectations, quarantine, SCD1 + SCD2), 6 notebooks
(Auto Loader, MERGE, CDF gold, streaming, DQ gate, batch backfill), Unity Catalog SQL (credentials, external
locations, volumes, 4 roles, column masks, row filter), Databricks SQL incl. system tables (job runs, query
history, billing), cluster policy. **Status: Implemented, cloud verification pending** — statically validated by
`tests/unit/test_bundle_config.py` (14 tests). Bug found in review: row-filter parameter shadowed by column.

**Phase 5 — API + monitoring** (`src/rideops/api/`, `src/rideops/monitoring/`)
- FastAPI with connection pool, typed responses, uniform JSON errors, request logging, pagination, OpenAPI docs.
- Endpoints: health (503 when DB down), analytics (overview, revenue, bookings, drivers, cancellations, payments),
  pipelines (list, history, logs, run detail, **real retry** via whitelisted job runner — 409 when disabled),
  monitoring summary, alerts (evaluate/acknowledge), incidents, costs (summary, trends, anomalies), data quality,
  settings/access.
- 6 alert rules with de-duplication, auto-resolve and optional webhook; incident history from Markdown;
  `simulate-incident` / `resolve-incident` for troubleshooting practice.
- **Tests:** 27 API tests against a throwaway database; full local suite 107 passed.
- Bugs found and fixed: positional `JSONResponse` argument broke all error handlers; dict-row vs tuple-row
  cursor in alert storage; session-scoped env var leaking between test modules.

## 3. Environment inventory (2026-11-18)

| Tool | Found | Notes / action |
|---|---|---|
| Python | 3.10.6 | Works, but end of life since Oct 2026. **Recommend installing 3.12** before Phase 1. |
| Git | 2.55.0 | OK |
| Node.js / npm | 24.20.0 / 11.19.0 | OK for Phase 7 |
| Docker | 29.7.2 | Docker Desktop started 2026-11-18; runs the `rideops-postgres` container. |
| Java | Temurin 21 at `C:\Users\chandu\tools\jdk21` | `JAVA_HOME` set, but `java` is not on `PATH`. Needed for Phase 3. |
| AWS CLI | 2.38.2 | Credentials present in the shared credentials file, region `ap-south-2`. **Not used until Phase 6, with permission.** |
| Terraform | Not installed | Needed in Phase 6 (`winget install Hashicorp.Terraform`) |
| Databricks CLI | Not installed | Needed in Phase 4 for bundle validation |
| PostgreSQL client | Not installed | Not required — will use `psql` inside the Docker container |
| GitHub CLI | Not installed | Optional |
| Free disk | ~40 GB | Enough; keep large generated datasets out of Git |

## 4. Open decisions

| # | Decision | Recommendation | Owner |
|---|---|---|---|
| D1 | Python version | Install 3.12, use a virtual environment | User |
| D2 | Spark runtime on Windows | Docker image (ADR-004) | Confirm in Phase 3 |
| D3 | Databricks workspace | Free Edition if available to the user; otherwise local equivalents only | User, Phase 4 |
| D4 | Hosting providers | Evaluate current free tiers in Phase 9 | Phase 9 |

## 5. Risks

- Native Spark on Windows needs Hadoop binaries → mitigated by Docker.
- Real AWS resources can cost money → Terraform runs `plan` only until the user approves `apply`; budget alarm documented.
- The Desktop-level Git repository could accidentally include this project if someone runs `git add .` on the Desktop.

## 6. Skills coverage matrix

Status labels: **Implemented and tested** · **Implemented, cloud verification pending** · **Configuration example provided** · **Simulated locally** · **Not implemented**

| # | Skill | Location | Status |
|---|---|---|---|
| 1 | Python | `src/rideops/config.py`, `src/rideops/generators/` | Implemented and tested (generators for all 10 source datasets) |
| 2 | SQL development and optimization | `sql/`, `docs/sql-optimization.md` | Implemented and tested |
| 3 | Apache Spark / PySpark | `src/rideops/pipelines/` | Not implemented |
| 4 | Databricks notebooks and workflows | `databricks/notebooks/`, `databricks/resources/` | Not implemented |
| 5 | Databricks SQL | `databricks/sql/` | Not implemented |
| 6 | Delta Lake | `src/rideops/pipelines/` | Not implemented |
| 7 | Medallion architecture | `src/rideops/pipelines/` | Not implemented |
| 8 | Unity Catalog | `databricks/unity_catalog/` | Not implemented |
| 9 | DLT / Lakeflow Declarative Pipelines | `databricks/dlt/` | Not implemented |
| 10 | Auto Loader | `databricks/notebooks/` | Not implemented |
| 11 | Structured Streaming | `src/rideops/streaming/`, `databricks/notebooks/` | Not implemented |
| 12 | AWS services | `src/rideops/storage/`, `infra/terraform/` | Not implemented |
| 13 | Databricks compute and permissions | `databricks/`, `docs/databricks-setup.md` | Not implemented |
| 14 | BI job and query monitoring | `src/rideops/monitoring/tracker.py`, `sql/analytics/08_pipeline_monitoring.sql`, `backend/` | Implemented and tested (tracking + SQL); API/UI pending |
| 15 | Cloud usage and cost reporting | `src/rideops/generators/ops.py` (simulated cost data), `src/rideops/costs/`, `sql/` | Simulated locally (data only; reporting not implemented) |
| 16 | Production-quality code and automated tests | `tests/`, `pyproject.toml` (ruff, pytest) | Implemented and tested (Phase 1a scope) |
| 17 | Git and GitHub | repository, `CONTRIBUTING.md`, PR template | Configuration example provided |
| 18 | GitHub Actions and YAML | `.github/workflows/` | Not implemented |
| 19 | Databricks Asset Bundles | `databricks/databricks.yml` | Not implemented |
| 20 | Terraform | `infra/terraform/` | Not implemented |
| 21 | JavaScript | `frontend/` | Not implemented |
| 22 | React.js | `frontend/` | Not implemented |
| 23 | TypeScript | `frontend/` | Not implemented |
| 24 | HTML5 and CSS3 | `frontend/` | Not implemented |
| 25 | Responsive web design | `frontend/` | Not implemented |
| 26 | REST APIs and JSON | `backend/` | Not implemented |
| 27 | Technical documentation and runbooks | `docs/`, `monitoring/runbooks/` | Not implemented (architecture doc started) |
| 28 | Production support and incident management | `monitoring/` | Not implemented |

## 7. Next steps

1. Phase 6: S3 storage backend, Terraform (S3 + IAM), cost documentation and optional AWS CUR loader.
2. Optional at any time: install Python 3.12 and recreate `.venv` (D1).
