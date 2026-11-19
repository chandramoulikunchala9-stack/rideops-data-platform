# Security

## Secrets

| Rule | How it is enforced |
|---|---|
| No credentials in source code | All configuration comes from environment variables / `.env` (git-ignored); `.env.example` documents names only |
| Database password has no default | `PostgresSettings.password` is required; Docker Compose refuses to start without `POSTGRES_PASSWORD` |
| Secrets never logged or returned | `SecretStr` hides the value in logs/`repr`; `/settings/access` shows the database host but not the password (tested) |
| AWS access without long-lived keys | AWS CLI profiles / SSO locally; GitHub OIDC role in CI; IAM role for Databricks |
| Databricks auth | `databricks auth login` (OAuth). No personal access tokens in files |
| Git hygiene | `.gitignore` covers `.env*`, `*.tfvars`, Terraform state, `.databricks/`, keys, data |

## Least privilege

**AWS IAM** (`infra/terraform/iam.tf`). Each policy is scoped to bucket **and** prefix:

| Policy | Can | Cannot |
|---|---|---|
| `landing-writer` (sources, CI) | `PutObject` to `raw/landing/*`, list that prefix | read, delete, touch other buckets |
| `pipeline` (Databricks UC role) | read `raw/landing/*`; read/write `processed`, `analytics` | change IAM or bucket policies |
| `analytics-read` (analysts group) | read `analytics` | anything else |

Trust policies limit **who** can assume a role. The Unity Catalog role requires Databricks' ExternalId,
which protects against confused deputies. The CI role is restricted to `repo:<owner>/<repo>:ref:refs/heads/main`.

**Bucket baseline:** public access blocked, ACLs disabled (`BucketOwnerEnforced`), SSE-S3 encryption,
and a deny on non-TLS requests.

## Data governance (Unity Catalog)

`databricks/unity_catalog/02_roles_and_grants.sql` defines 4 roles mapped to account groups:

| Role | Group | Access |
|---|---|---|
| Platform Administrator | `rideops-platform-admins` | Owns catalog, all privileges |
| Data Engineer | `rideops-data-engineers` | Read/write bronze, silver, gold, ops; volumes |
| Data Analyst | `rideops-data-analysts` | Read gold, plus selected silver tables with PII masked |
| Read-only User | `rideops-readonly` | Read gold |

Data access boundaries:
* **Column masks**: `email` and `phone` are masked for everyone except data engineers.
* **Row filter**: analysts see only the cities granted to them in `ops.analyst_city_access`.
* **Separate catalogs per environment** (`rideops_dev`, `rideops_prod`).
* Production jobs **run as a service principal**, not a person.

## Application security

* SQL injection: every query uses bound parameters. The few dynamic `ORDER BY` / `GROUP BY` parts come
  from whitelists or `Literal` types, never from raw user input.
* Input validation: FastAPI/Pydantic validate every parameter (patterns, ranges, enums) and return **422**.
* Errors: clients get `{"error": {code, message}}`. Stack traces stay in server logs.
* CORS: only the configured frontend origins (`API_CORS_ORIGINS`).
* The retry endpoint runs only whitelisted pipelines, is **off by default**, and refuses to run the same
  pipeline concurrently.
* Path traversal: `LocalStorage` rejects keys that escape its root (tested).
* Synthetic data only. Generated emails use reserved `example.*` domains.

## Known gaps (portfolio scope)

* The API has **no authentication**. It is read-mostly over synthetic data, and the only write action
  (retry) is disabled by default. Before exposing it publicly with real data, add OAuth2/OIDC (e.g. an
  identity provider JWT checked by a FastAPI dependency) and per-role authorisation.
* No rate limiting (add it at the hosting platform or a reverse proxy).
