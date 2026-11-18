# RideOps — Project Status

**Single source of truth for progress.** Updated at the end of every milestone.
Nothing is marked done unless it was built **and** verified as described in the "Verification" column.

Last updated: 2026-11-18 · Current phase: **Phase 0 complete → Phase 1 next**

---

## 1. Milestone checklist

| Phase | Milestone | Status |
|---|---|---|
| 0 | Repository, architecture, project structure | ✅ Done |
| 1 | Python package, config, logging, synthetic data generators, tests | ⏳ Next |
| 2 | PostgreSQL (Docker), schemas, data load, analytical SQL, optimization | ⬜ Not started |
| 3 | Local Spark, Bronze/Silver/Gold with Delta Lake, data-quality checks, transformation tests | ⬜ Not started |
| 4 | Databricks notebooks, SQL, Jobs, Unity Catalog, Auto Loader, Streaming, DLT, Asset Bundles | ⬜ Not started |
| 5 | FastAPI backend, monitoring, data-quality APIs, retry handling | ⬜ Not started |
| 6 | AWS S3 integration, IAM, cost analytics, Terraform | ⬜ Not started |
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
- [ ] First commit — left for the user as the Phase 0 exercise.

**Tests:** none yet (no code). **Verification:** `git rev-parse --show-toplevel` returns the project folder.

## 3. Environment inventory (2026-11-18)

| Tool | Found | Notes / action |
|---|---|---|
| Python | 3.10.6 | Works, but end of life since Oct 2026. **Recommend installing 3.12** before Phase 1. |
| Git | 2.55.0 | OK |
| Node.js / npm | 24.20.0 / 11.19.0 | OK for Phase 7 |
| Docker | 29.7.2 | Installed; **Docker Desktop was not running**. Needed from Phase 2. |
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
| 1 | Python | `src/rideops/` | Not implemented |
| 2 | SQL development and optimization | `sql/` | Not implemented |
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
| 14 | BI job and query monitoring | `src/rideops/monitoring/`, `backend/` | Not implemented |
| 15 | Cloud usage and cost reporting | `src/rideops/costs/`, `sql/` | Not implemented |
| 16 | Production-quality code and automated tests | `tests/` | Not implemented |
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

## 7. Next steps (Phase 1)

1. Install Python 3.12 (D1) and create `.venv`.
2. Add `pyproject.toml` with pinned dependencies, `ruff`, and `pytest`.
3. Build `rideops.config` (settings from environment variables) and `rideops.logging`.
4. Build generators for customers, drivers, vehicles, bookings, payments, ride/app events, cost and pipeline-run records,
   with configurable volume, a fixed seed, and deliberate data defects.
5. Unit-test reproducibility, row counts, referential integrity and defect rates.
