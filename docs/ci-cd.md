# CI/CD

> **Status:** workflows are written and statically checked with `actionlint` 1.7.12 (no findings).
> Every command they run has been run locally. They will execute on GitHub the first time the
> repository is pushed. Until then, "passes in CI" is not yet verified.

## Workflows (`.github/workflows/`)

| Workflow | Trigger (path-filtered) | Jobs | Local equivalent |
|---|---|---|---|
| `python.yml` | `src/`, `tests/`, `sql/`, `databricks/`, `pyproject.toml` | **lint** (ruff check + format) → **unit** (Python 3.10 and 3.12 matrix) → **backend** (PostgreSQL 17.5 service container: migrations, load, SQL ELT, *every named analytics query*, DQ checks, all API tests, CLI smoke test) | `ruff check src tests && pytest -m "not spark"` |
| `spark.yml` | `src/rideops/spark/`, `tests/spark/` | PySpark 4.2 + Delta 4.4 tests on Java 17, Ivy JAR cache | `docker compose run --rm spark pytest tests/spark` |
| `frontend.yml` | `frontend/` | `npm ci` → ESLint → `tsc` → Vitest → `vite build` (artifact uploaded) | `cd frontend && npm run lint && npm run typecheck && npm test && npm run build` |
| `infra.yml` | `infra/`, `databricks/` | `terraform fmt -check`, `init -backend=false`, `validate`; Databricks bundle static checks | see `docs/aws-setup.md` |
| `dependabot.yml` | weekly / monthly | Dependency update PRs for pip, npm, Actions, Terraform | – |

Design choices:
* **Path filters + `concurrency`**: only affected workflows run, and a newer push cancels the older run.
* **`permissions: contents: read`**: least privilege for the `GITHUB_TOKEN`.
* **Pinned tool versions** (Python deps, Node lockfile, Terraform 1.17.2, PostgreSQL 17.5), so CI equals local.
* **SQL validation** means running the SQL against a real PostgreSQL in CI, not just a syntax lint.
  `test_every_named_analytics_query_executes` runs all 45 analytics queries.
* **No deployment from CI by default.** Nothing here applies Terraform, deploys the Databricks bundle,
  or calls a hosting provider. That needs credentials and an explicit decision.

## Branching and pull requests

1. Branch from `main`: `feat/...`, `fix/...`, `docs/...` (see `CONTRIBUTING.md`).
2. Open a PR. The template asks how the change was verified and whether simulated data is labelled.
3. Protect `main` (GitHub → Settings → Branches): require the `Lint (ruff)`, `Unit tests`,
   `Backend, API and SQL tests`, `Lint, typecheck, test, build`, and `Terraform fmt + validate`
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
