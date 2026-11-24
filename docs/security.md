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

## RideOps Lens

Lens handles other people's database credentials, so it is built to a stricter standard than the demo
dashboard. Design reference: [ReContruction/lens-system-design.md](ReContruction/lens-system-design.md) §18.

| Concern | Control | Where |
|---|---|---|
| Passwords | Argon2id, 12+ characters, checked against the email; rehash on login when parameters change | `auth/passwords.py` |
| Sessions | Random token stored only as a hash; `HttpOnly`, `SameSite=Lax`, `Secure` cookies; idle and absolute expiry; new session id on every login; reset signs out everywhere | `auth/sessions.py` |
| CSRF | Double submit: mutating requests must echo the `lens_csrf` cookie in `X-CSRF-Token` | `auth/deps.py` |
| Login abuse | Failed-login lockout per email, per-IP limit (10/min); generic "email or password is incorrect"; forgot-password always returns 202 | `auth/router.py` |
| Email links | Single-use tokens stored hashed; verification 24 h, reset 30 min | `auth/sessions.py` |
| Authorisation | Roles owner / admin / editor / viewer checked on every route; other workspaces' resources return 404 | `workspaces/` |
| Tenant isolation | Postgres **Row-Level Security** on tenant tables, keyed by `app.workspace_id` set per transaction (tested with a non-superuser role) | migration 0002+ |
| Connection secrets | AES-256-GCM envelope encryption: a data key per secret, wrapped by a versioned master key (`LENS_MASTER_KEYS`); bound to workspace + connection; `rotate-keys` re-wraps; never returned or logged | `lens/security/crypto.py` |
| SSRF | Network Guard: resolve once, deny private, loopback, link-local, metadata, CGNAT and IPv6-mapped/6to4 forms, port allowlist, then **pin the IP** (no DNS rebinding); blocks are audited | `lens/security/netguard.py` |
| Dangerous URL options | `host=` sockets, `sslkey`, `sslrootcert`, `passfile`, `options` and similar are rejected | `lens/connections/url_parser.py` |
| TLS | Required by default; `sslmode=disable` only if a workspace owner opts in; insight flags production connections that don't verify certificates | `lens/connections/service.py` |
| Read-only access | SQL parsed with sqlglot (one read statement only) and Mongo pipelines checked; target sessions are also opened read-only with statement timeouts and row/byte caps | `lens/queries/` |
| Sensitive data | PII columns detected and masked in previews and results; revealing is a logged action | `lens/catalog/detect.py` |
| Demo isolation | The demo connection can never read Lens's `app` schema or role-password catalogs | `lens/queries/sqlguard.py` |
| Webhooks | Outgoing webhooks are signed (`X-Lens-Signature`, HMAC-SHA256) and go through the Network Guard | `lens/alerts/channels.py` |
| Logs | Passwords in URLs, `Authorization` and cookie headers, tokens and secrets are redacted | `hardening.py` |
| Abuse | 600 requests/min per session or IP; free-plan quotas on connections and check interval | `hardening.py`, `main.py` |
| Headers | CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, HSTS in production | `hardening.py` |
| Audit | Sign-ins, membership changes, connection changes, secret rotation, reveals and network blocks are recorded | `audit.py` |
| Operations | `/metrics` and `/internal/tick` require `INTERNAL_TOKEN` | `routers/lens_system.py` |

Checked on 2026-11-24: `pip-audit` found no known vulnerabilities in the Python dependencies, and `bandit`
reported no high-severity findings (the medium ones are SQL built from fixed identifiers).

## Known gaps (portfolio scope)

* The original RideOps endpoints (analytics, pipelines, monitoring, costs) are **public** by default: they
  are read-mostly over synthetic data, and the only write action (retry) is disabled by default. Set
  `LENS_PROTECT_RIDEOPS=true` to require a Lens sign-in for them as well.
* SQL Server connections are encrypted but the certificate is not verified (pymssql); MongoDB `+srv` hosts
  are checked by the Network Guard but not IP-pinned.
* No multi-factor authentication or single sign-on yet.
