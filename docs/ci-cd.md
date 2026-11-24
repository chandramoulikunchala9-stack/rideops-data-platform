# CI/CD

> **Status:** verified on GitHub on 2026-11-19. On the first push, all four workflows passed for `main`:
> Python, Spark, Frontend and Infrastructure. They are also statically checked with `actionlint` 1.7.12.

## Workflows (`.github/workflows/`)

| Workflow | Trigger (path-filtered) | Jobs | Local equivalent |
|---|---|---|---|
| `python.yml` | `pipelines/`, `backend/`, `databricks/`, `monitoring/`, `scripts/`, `ruff.toml` | **lint** (ruff over all Python) → **pipelines-unit** (Python 3.10 + 3.12 matrix) → **pipelines-integration** (PostgreSQL 17.5 service: migrations, load, SQL ELT, *every named analytics query*, DQ checks, CLI smoke test) and **backend** (Python 3.10 + 3.12 matrix; PostgreSQL + MySQL 8.4 + MongoDB 8.0 services: all API tests including Lens and the adapter contract suite; the 3.12 leg then builds the API image) | `ruff check pipelines backend scripts && pytest pipelines -m "not spark" && pytest backend` |
| `spark.yml` | `pipelines/src/rideops/spark/`, `pipelines/tests/spark/` | PySpark 4.2 + Delta 4.4 tests on Java 17, Ivy JAR cache | `docker compose --profile spark run --rm spark pytest pipelines/tests/spark` |
| `frontend.yml` | `frontend/` | `npm ci` → ESLint → `tsc` → Vitest → `vite build` (artifact uploaded) | `cd frontend && npm run lint && npm run typecheck && npm test && npm run build` |
| `infra.yml` | `infra/`, `databricks/` | `terraform fmt -check`, `init -backend=false`, `validate`; Databricks bundle static checks | see `docs/aws-setup.md` |
| `lens-tick.yml` | every 10 minutes (cron) and manual | `POST /internal/tick` on the hosted API, so free-tier instances wake up and run due Lens checks. Does nothing until the `LENS_API_URL` and `LENS_INTERNAL_TOKEN` repository secrets are set | `curl -X POST -H "X-Internal-Token: $INTERNAL_TOKEN" http://localhost:8000/internal/tick` |
| `dependabot.yml` | weekly / monthly | Dependency update PRs for pip, npm, Actions, Terraform | – |

Design choices:
* **Path filters + `concurrency`**: only affected workflows run, and a newer push cancels the older run.
* **`permissions: contents: read`**: least privilege for the `GITHUB_TOKEN`.
* **Pinned tool versions** (Python deps, Node lockfile, Terraform 1.17.2, PostgreSQL 17.5), so CI equals local.
* **SQL validation** means running the SQL against a real PostgreSQL in CI, not just a syntax lint.
  `test_every_named_analytics_query_executes` runs all 45 analytics queries.
* **Real databases for Lens.** The backend job's MySQL and MongoDB service containers start empty; tests seed
  what they read. SQL Server is skipped in CI (2 GB image) and runs locally.
* **No deployment from CI by default.** Nothing here applies Terraform, deploys the Databricks bundle,
  or calls a hosting provider. That needs credentials and an explicit decision. (`lens-tick` only calls an
  already-deployed API.)

## Branching and pull requests

1. Branch from `main`: `feat/...`, `fix/...`, `docs/...` (see `CONTRIBUTING.md`).
2. Open a PR. The template asks how the change was verified and whether simulated data is labelled.
3. Protect `main` (GitHub → Settings → Branches): require the `Lint (ruff)`, `Pipelines unit tests`,
   `Pipelines + SQL integration`, `Backend API tests` (both Python versions), `Lint, typecheck, test, build`, and `Terraform fmt + validate`
   checks, and at least one review.
4. Squash-merge with a Conventional Commit title.

## Optional: continuous deployment

When you want deployments from CI, add a separate workflow that runs only on `main` and on a
protected **GitHub Environment** with required reviewers:

| Target | How | Credentials |
|---|---|---|
| AWS (Terraform) | `terraform plan` on PRs (comment the plan), `apply` on `main` after approval | GitHub OIDC role from `infra/terraform/iam.tf` (no stored keys) |
| Databricks | `databricks bundle validate` on PRs, `deploy -t prod` on `main` | Service principal OAuth (`DATABRICKS_CLIENT_ID/SECRET` as environment secrets) |
| Backend / frontend hosting | Provider deploy hook or Git integration | Provider token as an environment secret |
