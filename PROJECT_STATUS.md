# RideOps — Project Status

**Single source of truth for progress.** Nothing is marked done unless it was built **and** verified
as described. Cloud items say exactly what was and was not verified.

Last updated: 2026-11-19 · State: **all phases implemented; cloud deployments prepared but not performed**
(they need the owner's AWS, Databricks and hosting accounts).

---

## 1. Milestones

| Phase | Milestone | Status | Evidence |
|---|---|---|---|
| 0 | Repository, architecture, structure | ✅ Done | Separate repo; monorepo layout (see README) |
| 1 | Synthetic data generators | ✅ Done | 10 datasets, manifest, reproducible (byte-identical reruns) |
| 2 | PostgreSQL, SQL ELT, analytics SQL, optimisation | ✅ Done | 45 queries executed in tests; EXPLAIN ANALYZE in `docs/sql-optimization.md` |
| 3 | Spark + Delta medallion lakehouse, streaming | ✅ Done | 15 Spark tests; full-data run; **SQL↔Spark reconcile 0/540** |
| 4 | Databricks (bundle, jobs, DLT, Auto Loader, UC) | ✅ Written + statically validated | Workspace deployment pending |
| 5 | FastAPI backend, monitoring, alerts, incidents | ✅ Done | 27 API tests; real retry verified |
| 6 | AWS S3/IAM, Terraform, costs, CUR loader | ✅ Written + validated | `terraform validate` passes; not applied |
| 7 | React + TypeScript dashboard | ✅ Done | 21 tests; screenshots; responsive at 390 px |
| 8 | GitHub Actions CI | ✅ Done | **All 4 workflows green on GitHub** for `main` (2026-11-19) |
| 9 | Deployment | ✅ Local Docker deployment verified (12/12) | Hosted: prepared, not deployed |
| 10 | Interview preparation guide | ✅ Done | `docs/interview-guide.md` |
| – | Folder restructure into `pipelines/` + `backend/` packages | ✅ Done | 107 files moved with history; all suites re-run |
| 11 | **Lens** foundations: Alembic + `app` schema, auth, workspaces, RBAC, audit, CSRF, job queue + scheduler | ✅ Backend done (`dev` branch) | 37 Lens tests (auth e2e, tenancy 404 on every workspace route, RBAC, **4 concurrent workers claim 200 jobs exactly once**, scheduler leader lock, migrations up/down); existing API tests still green; uvicorn smoke run with `LENS_ROLE=all`. Postgres RLS (FR-04 defence in depth) is deferred to Phase 12, which adds the first tenant data tables (connections). |
| 12 | Lens: connections + PostgreSQL adapter + fleet UI | ⏳ Not started | |
| 13 | Lens: analytics (schema explorer, profiling, query console) | ⏳ Not started | |
| 14 | Lens: alerts + MySQL / SQL Server / SQLite / MongoDB adapters | ⏳ Not started | |
| 15 | Lens: hardening + hosted deploy | ⏳ Not started | |

## 2. Repository structure (after the restructure, 2026-11-19)

| Folder | Contents |
|---|---|
| `pipelines/` | Python package `rideops`: generators, ingestion, SQL pipeline (`db`), lakehouse (`spark`), quality, monitoring, costs, storage; `sql/`; `tests/`; Spark `Dockerfile` |
| `backend/` | Python package `rideops_api` (FastAPI): routers, schemas, pool, errors, retry runner; `tests/`; `Dockerfile` |
| `frontend/` | React + TypeScript dashboard |
| `databricks/` | Asset Bundle, jobs, DLT, notebooks, Unity Catalog, Databricks SQL, cluster policy |
| `infra/terraform/` | AWS S3, IAM, Budgets, CUR export |
| `monitoring/` | runbooks (8), incident history (4) |
| `scripts/`, `docs/`, `.github/` | deployment verification, documentation, CI |

The restructure also fixed two latent issues: the Databricks bundle's wheel library paths did not
point at the artifact's `dist/` (a regression test now checks this), and the API image located
`sql/` relative to `site-packages` (now configured with `RIDEOPS_SQL_DIR`).

## 3. Verification log (latest results)

| Check | Command | Result |
|---|---|---|
| Python lint/format | `ruff check pipelines backend scripts` / `ruff format --check ...` | clean |
| Pipelines tests (incl. PostgreSQL, full-scale generator, S3 via moto, CUR, bundle) | `pytest pipelines -m "not spark"` | 90 passed |
| Backend API tests | `pytest backend` | 27 passed |
| Spark + Delta tests | `docker compose --profile spark run --rm spark pytest pipelines/tests/spark` | 15 passed (re-run after restructure: see section 3a) |
| Frontend | `npm run lint && npm run typecheck && npm test && npm run build` | clean · 21 passed · build OK |
| Terraform | `terraform fmt -check`, `init -backend=false`, `validate` (1.17.2, AWS provider 6.73.0) | valid |
| Workflows | `actionlint` 1.7.12 | no findings |
| Local deployment | `scripts/verify_deployment.py --api :8001 --web :8080` | 12/12 (re-verified after restructure) |
| Full-data SQL pipeline | `python -m rideops.db all` | 99,187 bookings, 813 quarantined; reruns are no-ops |
| Full-data lakehouse | `python -m rideops.spark all` (Docker, ~1h50m on 8 GB laptop) | silver identical to SQL core; 13/13 lakehouse DQ PASS |
| Reconciliation | `ops.reconcile_lakehouse_daily()` | 0 / 540 date×city differences; identical totals |
| Incident drill | simulate → load → FAIL → alert → resolve → PASS → alert auto-resolved | verified end to end |

### 3a. After the restructure and push (2026-11-19)
* Spark suite from `pipelines/tests/spark` in the rebuilt image: **15 passed** (6 min 41 s).
* Pushed to `github.com/chandramoulikunchala9-stack/rideops-data-platform` (`main`, fast-forward).
* GitHub Actions on `main`: **Python ✅ · Spark ✅ · Frontend ✅ · Infrastructure ✅**. This is the first
  verified CI run on GitHub (PostgreSQL service tests, Spark on Java 17, `terraform validate`, frontend build).
* Dependabot opened 6 PRs. 5 pass CI. The PySpark 4.2.1 bump fails at `pip install` because delta-spark 4.4.1
  requires `pyspark <= 4.2.0`: CI correctly blocked an incompatible upgrade. Dependabot now ignores
  lone PySpark bumps (upgrade PySpark and Delta together).

### 3b. Cloud deployment attempts (2026-11-19)
* **AWS:** `terraform plan` against account `…5280` (ap-south-2): 25 to add, 0 to change, 0 to destroy. `apply`
  was rejected by IAM (the user lacks `s3:CreateBucket`, `iam:CreateGroup`, `budgets:ModifyBudget`), so **nothing
  was created**. The state holds only the local `random_id`. The apply also revealed an invalid S3 tag value (fixed).
  Next: attach `infra/terraform/deployer-policy.json`, then re-run plan and apply.
* **Databricks:** CLI v1.26.0 installed; `bundle validate` waits for a workspace login (`databricks auth login`).
* **Hosting:** waits for Neon / Render / Cloudflare accounts.

## 4. Bugs found and fixed during verification (selection)

| Where | Bug | How it was caught |
|---|---|---|
| DQ check | Counted reasons instead of rows (4 vs 813) | Reconciling against an independent query → INC-2026-004 |
| Spark quality | `Column` objects created at import time | Collection error in the Spark suite |
| Delta demo | `count()` answered from log stats after VACUUM | Test expectation failed; switched to `collect()` |
| Streaming test | Late-event semantics with 2 stateful operators | Debug run printing per-batch watermarks |
| API errors | Positional `JSONResponse(404, ...)` broke every handler | Error-case smoke tests |
| Alerts | Tuple vs dict rows from the API pool | API test |
| Tests | Session env var leaking between modules | Full-suite vs isolated run difference |
| Unity Catalog | Row-filter parameter shadowed by a column (always true) | Code review |
| Dashboard | Grid min-content overflow on phones; colliding chart labels; misleading "5 failures" KPI | Screenshot review |
| Bundle | Wheel library path ≠ artifact output | Found while restructuring; regression test added |
| Terraform | S3 tag values with `(),` rejected by AWS (`InvalidTag`) | First real `apply`; `validate` cannot catch it |

## 5. Environment inventory

| Tool | Version | Notes |
|---|---|---|
| Python | 3.10.6 local (CI also 3.12); 3.11 in Spark image; 3.12 in API image | 3.10 is end-of-life; upgrading to 3.12 is recommended |
| Docker | 29.7.2 | Runs PostgreSQL 17.5, Spark image, app stack, Terraform, actionlint |
| Node / npm | 24.20 / 11.19 (CI: Node 22) | |
| Java | Temurin 21 on host (unused); Java 17 in the Spark image | |
| AWS CLI | 2.38.2, credentials present | **Never used**: no AWS resources were created |
| Host RAM | 7.8 GB, at times < 0.1 GB free | Main reason Spark runs are slow |

## 6. Skills coverage matrix

Labels: **Implemented and tested** · **Implemented, cloud verification pending** · **Configuration example provided** · **Simulated locally** · **Not implemented**

| # | Skill | Exact location | Status |
|---|---|---|---|
| 1 | Python | `pipelines/src/rideops/**`, `backend/src/rideops_api/**` | Implemented and tested |
| 2 | SQL development & optimisation | `pipelines/sql/` (ddl, transform, 45 analytics queries, checks), `docs/sql-optimization.md` | Implemented and tested |
| 3 | Apache Spark / PySpark | `pipelines/src/rideops/spark/`, `pipelines/tests/spark/` | Implemented and tested |
| 4 | Databricks notebooks & workflows | `databricks/notebooks/01–06`, `databricks/resources/rideops_jobs.yml` | Implemented, cloud verification pending |
| 5 | Databricks SQL | `databricks/sql/01_gold_dashboards.sql`, `02_platform_monitoring_system_tables.sql` | Implemented, cloud verification pending |
| 6 | Delta Lake | `pipelines/src/rideops/spark/{bronze,silver,gold,delta_demo}.py` (ACID, MERGE, CDF, time travel, OPTIMIZE, VACUUM) | Implemented and tested |
| 7 | Medallion architecture | Lakehouse bronze/silver/gold + SQL raw/core/analytics | Implemented and tested |
| 8 | Unity Catalog | `databricks/unity_catalog/01_catalog_and_schemas.sql`, `02_roles_and_grants.sql` | Implemented, cloud verification pending |
| 9 | DLT / Lakeflow Declarative Pipelines | `databricks/dlt/rideops_pipeline.py`, `databricks/resources/rideops_pipeline.yml` | Implemented, cloud verification pending |
| 10 | Auto Loader | `databricks/notebooks/01_bronze_autoloader.py`, `04_streaming_ride_events.py`, DLT sources | Implemented, cloud verification pending (local equivalent: file stream + checkpoint, tested) |
| 11 | Structured Streaming | `pipelines/src/rideops/spark/streaming.py` (watermark, dedup, windows, checkpoints, replay) | Implemented and tested |
| 12 | AWS services | `pipelines/src/rideops/storage/` (S3), `costs/cur_loader.py`, `infra/terraform/` | Implemented, cloud verification pending (S3 tested with moto) |
| 13 | Databricks compute & permissions | Job clusters, autoscale, spot, serverless, `policies/job_cluster_policy.json`, `run_as` SP, bundle permissions, UC grants | Configuration example provided |
| 14 | BI job & query monitoring | `pipelines/src/rideops/monitoring/`, `pipelines/sql/analytics/08_pipeline_monitoring.sql`, backend pipelines/monitoring routers, `databricks/sql/02_*` | Implemented and tested (Databricks system tables: cloud verification pending) |
| 15 | Cloud usage & cost reporting | `ops.cloud_costs`, `pipelines/sql/analytics/09_cloud_costs.sql`, `backend/src/rideops_api/routers/costs.py`, Costs page, `cur_loader.py` | Simulated locally (real CUR / system-table paths: cloud verification pending) |
| 16 | Production-quality code & tests | 132 Python tests (+15 Spark) + 21 frontend tests, ruff, typed code, uniform errors, retries, logging | Implemented and tested |
| 17 | Git & GitHub | Repo on GitHub, Conventional Commits, `CONTRIBUTING.md`, PR template, Dependabot (6 PRs opened) | Implemented and tested |
| 18 | GitHub Actions & YAML | `.github/workflows/{python,spark,frontend,infra}.yml` | Implemented and tested (green on GitHub) |
| 19 | Databricks Asset Bundles | `databricks/databricks.yml`, `resources/*.yml`, `pipelines/tests/unit/test_bundle_config.py` | Implemented, cloud verification pending (static checks pass) |
| 20 | Terraform | `infra/terraform/*.tf` | Implemented, cloud verification pending (validate passes) |
| 21 | JavaScript | `frontend/eslint.config.js`, build tooling, runtime behaviour | Implemented and tested |
| 22 | React.js | `frontend/src/{pages,components}` | Implemented and tested |
| 23 | TypeScript | `frontend/src/**/*.ts(x)` (strict mode) | Implemented and tested |
| 24 | HTML5 & CSS3 | `frontend/index.html`, `src/styles/{tokens,app}.css` (semantic HTML, CSS variables, grid) | Implemented and tested |
| 25 | Responsive design | `app.css` media queries, `minmax(0, 1fr)` grids; verified at 390 px | Implemented and tested |
| 26 | REST APIs & JSON | `backend/src/rideops_api/` (22 endpoints, OpenAPI) | Implemented and tested |
| 27 | Technical documentation & runbooks | `docs/*.md`, `monitoring/runbooks/` (8), READMEs per project | Implemented |
| 28 | Production support & incident management | `monitoring/incidents/` (4), alert rules, simulate/resolve drill, runbooks | Implemented and tested (drill verified) |

## 7. Not done / next steps (owner decisions)

1. ~~Push to GitHub and run CI~~ (done). Protect `main` with required checks; review the Dependabot PRs.
2. **Hosted deployment** (Neon + Render + Cloudflare Pages) following `docs/deployment.md`, then run
   `scripts/verify_deployment.py` against the URLs and record the result here.
3. **AWS**: review `terraform plan`, then apply in a dev account with a small budget.
4. **Databricks**: Free Edition or a trial workspace → `databricks bundle validate/deploy -t dev`.
5. Upgrade the local Python to 3.12. Add API authentication before exposing it with real data.
