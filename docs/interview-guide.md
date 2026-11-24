# RideOps — Interview Preparation Guide

This guide prepares you to explain RideOps end to end. It covers what each part does, why it was built
that way, how to demo it, and how to answer likely questions. Every number here was measured on this
project, so you can quote it.

**How to use it:** read Part 1 until you can say it without notes. Use Part 2 to understand each
component, practise the demo in Part 3, and work through the questions in Part 4 out loud.

---

## Part 1 — The pitches

### 1.1 Two-minute introduction

> "RideOps is an end-to-end data platform I built for a fictional ride-booking company in six Indian
> cities. I generate realistic synthetic data on purpose, including dirty data: duplicates, nulls,
> malformed emails, legacy timestamp formats, orphan keys and late-arriving events. Then I process it
> through **two independent pipelines**: a PostgreSQL SQL warehouse, and a PySpark plus Delta Lake medallion
> lakehouse with bronze, silver and gold layers.
>
> Both are incremental. Files are ingested exactly once using an ingestion log in SQL and streaming
> checkpoints in Spark, upserts use MERGE, and the gold layer uses Delta's Change Data Feed to recompute
> only the dates that changed. Because I implemented the same business rules twice, I could **reconcile**
> them: on 100,000 bookings, all 540 date-by-city cells match exactly, down to the rupee.
>
> On the operations side, every run is tracked. I have 31 data-quality checks, alert rules for failed
> or long-running jobs, stale data, cost anomalies and budget overruns, plus runbooks, incident history and
> a simulated incident I can trigger and resolve live. A FastAPI backend serves all of this to a React and
> TypeScript dashboard, including a retry button that really re-runs a pipeline.
>
> I also wrote the Databricks version, with an Asset Bundle, Lakeflow jobs with a quality gate, a
> declarative pipeline, Auto Loader and Unity Catalog permissions, plus Terraform for S3 and IAM and
> GitHub Actions CI. Those cloud parts are validated locally but not yet deployed to a real account, and
> I'm explicit about that in the repo.
>
> On top of that I built **RideOps Lens**, a multi-tenant SaaS layer: people sign up, create a workspace,
> paste a connection URL for PostgreSQL, MySQL, SQL Server, MongoDB or a SQLite file, and get health
> monitoring, a schema explorer, profiling, a read-only query console, dashboards and alerts. Because it
> handles other people's credentials, it has Argon2id passwords, server-side sessions with CSRF,
> role-based access, Postgres Row-Level Security, encrypted secrets with key rotation, and SSRF protection
> that pins the resolved IP. The background work runs on a Postgres job queue with `SKIP LOCKED`."

### 1.2 Ten-minute technical walkthrough (structure)

| Minute | Topic | Key points to hit |
|---|---|---|
| 0–1 | Problem | Messy multi-source data; the business needs trusted numbers, and ops needs to know when things break and what they cost |
| 1–2 | Architecture | Show the README diagram: landing → two pipelines → Postgres → API → UI; `ops` schema for metadata |
| 2–3 | Data generation | Seeded per-entity RNG, exact defect counts in a manifest, so quality checks can be **verified**, not eyeballed |
| 3–5 | SQL pipeline | COPY with schema contracts; raw stays text; `ROW_NUMBER` latest-version dedup; quarantine vs `dq_flags`; MERGE; watermark incrementality; indexes measured with EXPLAIN ANALYZE |
| 5–7 | Lakehouse | Bronze file streams with `availableNow`; silver `foreachBatch` MERGE; gold via Change Data Feed + `replaceWhere`; streaming windows with watermark; reconciliation 0/540 |
| 7–8 | Operations | Run tracker, 31 checks, 6 alert rules (dedup + auto-resolve), incident drill, runbooks |
| 8–9 | API + UI | Pool, uniform errors, real whitelisted retry; dashboard with validated palette, table views, URL filters |
| 9–10 | Cloud + CI + honesty | Databricks bundle, Terraform, CI; what is verified vs pending; the bugs found and how |
| (+3) | Lens | Add a connection by URL (staged test), status + score, schema → profile → query console (DELETE refused), an alert rule firing; then security: RLS, envelope encryption, Network Guard |

### 1.3 Numbers worth remembering

| Fact | Value |
|---|---|
| Bookings (full scale) | 100,000 + 1,000 duplicate deliveries → **99,187 kept, 813 quarantined** |
| Payments | 86,905 rows → 84,924 kept, 1,121 quarantined |
| Ride events / app events | 372,918 (92 daily files) / 355,970 |
| SQL vs Spark reconciliation | **0 of 540** date×city cells differ; ₹13,095,490.51 in both |
| Data-quality checks | 18 SQL + 13 Spark |
| Analytics queries | 45 named queries in 9 SQL scripts |
| Index speed-ups (measured) | 48× (composite index), 16× (join indexes), 128× (sargable range) |
| Generator speed-up after profiling | 98 s → 26 s (orjson, faster IDs, `isoformat`) |
| Tests | 90 pipelines + 195 API (incl. Lens) + 15 Spark + 43 frontend |
| Lens | 98 API operations, 5 engines, adapter contract 25/25, SSRF corpus 100 % blocked, axe-core 0 violations on 14 pages |
| Dashboard bundle after code splitting | 874 kB → 267 kB initial |
| Full lakehouse run on an 8 GB laptop | about 1 h 50 m (streaming 92 micro-batches was the bulk) |

---

## Part 2 — Understand every component

### 2.1 Repository layout (say it like this)
"It's a monorepo with one folder per deployable part. `pipelines/` is the Python data platform
package; `backend/` is the FastAPI service, which uses `pipelines` as a library; `frontend/` is the
React app; `databricks/` is the bundle; `infra/terraform/` is AWS; and `monitoring/` holds runbooks
and incidents. Lens lives inside `backend/` and `frontend/`, with its own `app` schema and migrations."

### 2.2 Synthetic data (`pipelines/src/rideops/generators/`)
- **Why synthetic?** No real customer data, but realistic patterns: rush hours in IST, a weekend uplift,
  growth over time, surge pricing, a fare formula, and cancellations with reasons.
- **Reproducibility:** each entity has its own RNG seeded from `"{seed}-{entity}"`, so adding a new
  dataset never changes existing ones. Two runs with the same seed are byte-identical (verified with `diff`).
- **Defects with exact counts:** for example, 0.5% invalid gives *exactly* `round(n × 0.005)` rows, recorded
  in a manifest. Missing values never overlap invalid ones, so the counts stay exact. That lets you
  *prove* a quality check caught everything.
- **Referential and temporal integrity:** no booking before the customer signed up, and drivers only
  take rides after joining. Payments are derived from *clean* bookings, so defects don't cascade.
- **Atomic writes:** each file is written to a temporary name, then renamed, so an Auto Loader–style
  reader never sees a half-written file.

### 2.3 SQL warehouse (`pipelines/src/rideops/{ingestion,db}`, `pipelines/sql/`)
- **Schemas:** `raw` (text/jsonb as delivered) → `core` (typed, constrained) → `analytics` (gold), plus `ops`.
- **Loader:** `COPY` streaming; the header or keys are checked against the source contract; the data and
  the `ops.ingestion_log` row are committed **in one transaction**; transient errors are retried with
  backoff; schema mismatches are recorded as failed files instead of crashing the run.
- **Transform pattern:** stage rows newer than the watermark → `ROW_NUMBER() OVER (PARTITION BY key
  ORDER BY updated_at DESC)` → quarantine untrusted version-1 rows → `MERGE ... WHEN MATCHED AND
  s.updated_at > t.updated_at` → advance the watermark. This is all in one transaction per file.
- **Quarantine vs repair (a key design decision):** a malformed email becomes NULL plus a
  `malformed_email` flag, and the customer is kept. A negative fare or an orphan key sends the row to
  quarantine. "Don't throw away a whole customer for one bad attribute."
- **Business date:** a generated column computes `booking_date` in IST, because a ride at 02:00 IST is
  that Indian business day.
- **Optimisation:** a composite index (equality column first), join indexes on foreign keys, sargable
  ranges, partial indexes, `FILTER` aggregates. See `docs/sql-optimization.md` for real plans.

### 2.4 Lakehouse (`pipelines/src/rideops/spark/`)
- **Bronze:** `readStream` over the landing files with an explicit all-string schema,
  `trigger(availableNow=True)` and a checkpoint. That's the open-source equivalent of Auto Loader:
  each file is processed once, and the same job can run as a batch or as a stream.
- **Silver:** a Delta streaming source → `foreachBatch` → the same rules as SQL (pure functions) →
  quarantine append + `MERGE`. Dimensions run first, so facts are validated against them.
- **Gold:** reads the **Change Data Feed** since the last processed version, collects the affected
  `booking_date`s, and rewrites only those with `replaceWhere`. Small tables are rebuilt in full.
- **Streaming:** event time and processing time are different things. A 30-minute watermark,
  `dropDuplicatesWithinWatermark` on `event_id`, and 15-minute windows in append mode.
- **Delta features (`delta_demo.py`):** a failed write leaves the table unchanged (ACID); a wrong type is
  rejected (enforcement); `mergeSchema` adds a column (evolution); MERGE; time travel; history;
  OPTIMIZE ZORDER; VACUUM.
- **Why Docker?** Spark on Windows needs Hadoop native binaries, while the Linux image matches CI.

### 2.5 Databricks (`databricks/`)
- **Bundle:** dev and prod targets; dev uses `mode: development` with paused schedules; prod `run_as` a
  service principal; the wheel artifact is built from `pipelines/`.
- **Jobs:** bronze → silver → **quality gate** → gold. The gate raises an error on a FAIL, so gold
  never refreshes from bad data. Also: retries, timeouts, duration alerts, `max_concurrent_runs: 1`, and
  autoscaling job clusters with spot workers.
- **DLT:** expectations (`expect`, `expect_or_drop`, `expect_or_fail`), a quarantine table built from the
  inverted rules, `apply_changes` for SCD1 bookings and **SCD2 customer history**.
- **Unity Catalog:** catalog per environment, schemas per layer, volumes for files, 4 group-based roles,
  **column masks** (email/phone) and a **row filter** (cities per analyst).
- **Honest framing:** "The notebooks import the same tested transformation functions as the local
  pipeline, so the logic is tested. The Databricks wiring is statically validated but not yet run in a
  workspace."

### 2.6 Monitoring and operations (`pipelines/src/rideops/monitoring/`, `monitoring/`)
- **RunTracker:** inserts a RUNNING row on its own autocommit connection, then updates it to SUCCESS
  or FAILED with counts and the error. Failures are recorded even when the work's transaction rolls back.
- **Alerts:** each rule is a SQL query; alerts are de-duplicated on (rule, subject), auto-resolve when the
  condition clears, and only *new* alerts go to the webhook, so nobody gets paged twice.
- **Incident drill:** `simulate-incident` drops a schema-drifted file → the loader rejects it → the
  check FAILs → a CRITICAL alert fires → `resolve-incident` quarantines the file → PASS → the alert
  auto-resolves.

### 2.7 Costs (`ops.cloud_costs`, `pipelines/src/rideops/costs/`)
- Every row has a `source` (simulated / aws_cur / databricks_system_tables), and the UI shows SIMULATED.
- Anomalies: a z-score above 3 over the trailing 14 days. Budget: a linear run-rate forecast against the
  budget. Untagged spend is tracked as a data-quality check.
- The real-data path: a CUR 2.0 export (Terraform) → `load-cur`, and Databricks `system.billing.usage`.

### 2.8 API (`backend/src/rideops_api/`)
- Connection pool, Pydantic response models (OpenAPI at `/docs`), one JSON error shape, request IDs and
  timing logs, pagination, whitelisted `ORDER BY`.
- **Retry endpoint:** it really runs whitelisted local pipelines, returns 202, and the new run is recorded
  with `trigger=retry` and `retry_of_run_id`. If the runner is disabled it returns 409, and for Spark jobs
  it returns 400 with the reason. It never fakes success.
- `/health` returns 503 when the database is down, so load balancers stop routing to the instance.

### 2.9 Dashboard (`frontend/`)
- A typed client mirrors the backend schemas. `useApi` derives `loading`, aborts stale requests and
  keeps old data visible while new filters load.
- Filters live in the URL (shareable views). The chart palette was validated for colour-vision
  deficiency in light and dark mode. Every chart has a legend and a table view. Status badges always
  show an icon and a label.
- Route-based code splitting. Responsive layout verified at 390 px.

### 2.10 Infrastructure and CI (`infra/terraform/`, `.github/workflows/`)
- **S3:** public access blocked, SSE-S3, TLS-only bucket policy, BucketOwnerEnforced, versioning and
  lifecycle tiering.
- **IAM:** policies scoped by bucket **and prefix**; the UC role uses an ExternalId and self-assume; the
  GitHub OIDC role has no stored keys and is limited to the main branch; an AWS Budget alerts at 80/100%.
- **CI:** ruff, a Python 3.10/3.12 matrix, PostgreSQL service containers that *execute* every SQL query,
  Spark on Java 17, the frontend build, and Terraform validate. Path filters, concurrency
  cancellation, and least-privilege `GITHUB_TOKEN`.

### 2.11 RideOps Lens (`backend/src/rideops_api/{auth,workspaces,jobs,lens}/`, `frontend/src/lens/`)
- **Why it exists:** turns a one-company dashboard into a product anyone can point at their own database.
- **Tenancy:** every route is scoped by workspace and role; Postgres RLS is the second wall, so a missed
  `WHERE workspace_id` still returns nothing. Other workspaces' resources return 404.
- **Secrets:** AES-256-GCM envelope encryption, key ring with versions, AAD binds the ciphertext to its
  workspace and connection, `rotate-keys` re-wraps without downtime.
- **SSRF:** a connection URL is attacker-controlled input. The Network Guard resolves once, refuses private
  and metadata ranges, then connects to the pinned IP so DNS rebinding can't switch targets.
- **Read-only, twice:** sqlglot AST check plus read-only sessions with timeouts and caps.
- **Jobs:** Postgres queue (`FOR UPDATE SKIP LOCKED`, idempotency keys, backoff, heartbeats, a reaper) and a
  scheduler elected by an advisory lock; one image runs as API, worker, scheduler or all three.
- **Engines:** one adapter contract, verified by the same 5 tests against every engine.
- **Alerts:** rule types for status, metrics, freshness, row drops, schema changes and privileges; de-duplication,
  two-check auto-resolve, maintenance windows, incidents, HMAC-signed webhooks.

---

## Part 3 — Live demo script (8 minutes)

1. `docker compose up -d postgres` → `uvicorn rideops_api.main:app` → `npm run dev` in `frontend/`.
2. **Overview:** KPIs, the six city lines (point out "colour follows the city"), and the SIMULATED badge.
3. **Analytics:** switch to a 7-day range, filter Hyderabad, toggle a chart to its table view.
4. **Pipelines → ingest_payments:** open the 19 Oct failed run; show the error, the retries, and incident
   INC-2026-001 with its resolution notes.
5. **Retry:** click Retry on `pg_quality_checks` → the new run appears with `trigger=retry`.
6. **Incident drill** (terminal): `simulate-incident` → `rideops.db all` → Data quality shows FAIL →
   Alerts shows CRITICAL → `resolve-incident` → re-run → the alert disappears.
7. **Costs:** the anomaly points on Oct 1–2 and the over-budget status. Explain that it's simulated, and
   how CUR data would arrive labelled.
8. **Code:** `pipelines/sql/transform/20_core_facts.sql` (MERGE) and `spark/gold.py` (CDF). Then
   `ops.reconcile_lakehouse_daily()` → 0/540.
9. **Lens** (with `LENS_ENABLED=true`): sign up → the RideOps demo connection turns healthy → schema →
   profile a table → run a query, then try `DELETE` and show it refused → add a connection to
   `169.254.169.254` and show `CONN_BLOCKED_NETWORK`.

---

## Part 4 — Questions and answers

### Python
**Q: How do you make generated data reproducible?** A separate `random.Random` per entity, seeded with
a string. String seeds are hashed deterministically, unlike Python's `hash()`. The same seed produces
byte-identical files, and I verified that.

**Q: How did you speed up the generator?** I profiled first with cProfile. The time was in `strptime`,
`strftime`, `json.dumps` and `uuid`. I switched to `fromisoformat`/`isoformat`, orjson, and formatted the
random bits directly. That took it from 98 s to 26 s, and I proved the output was identical with `diff`.

**Q: How do you handle configuration and secrets?** pydantic-settings reads environment variables and
`.env`; the password is a `SecretStr` with no default; `.env` is git-ignored; AWS access uses profiles,
roles or OIDC.

**Q: How do you handle transient failures?** A `retry` decorator with exponential backoff that retries
only listed exceptions, such as `OperationalError`. A schema mismatch is permanent, so it fails fast.

### SQL
**Q: How do you deduplicate when a source re-sends records?** `ROW_NUMBER() OVER (PARTITION BY key ORDER
BY updated_at DESC, _loaded_at DESC)` and keep rank 1. The MERGE only updates when `s.updated_at >
t.updated_at`, so a late re-delivery of an old version can't overwrite newer data.

**Q: `NOT IN` vs `NOT EXISTS`?** `NOT IN` returns no rows at all if the subquery contains a NULL.
`NOT EXISTS` is safe and is planned as an anti-join.

**Q: A query got slow. What do you do?** Run `EXPLAIN (ANALYZE, BUFFERS)`, find the most expensive node,
compare estimated and actual rows (stale statistics → `ANALYZE`), check that predicates appear as an
`Index Cond`, check join types, look for sorts spilling to disk. Example from this project: wrapping
`processed_at` in a function made the query go from 0.19 ms to 23.7 ms.

**Q: Explain a window function you used.** `LAG` for month-over-month growth and the gaps between
rides; `RANK` vs `DENSE_RANK` for driver leaderboards; `FIRST_VALUE` for cohort retention; a running
count for the gaps-and-islands "failures since last success"; `stddev` over a window for cost anomalies.

### PySpark / Delta
**Q: How is your bronze layer incremental?** A file stream with a checkpoint and `availableNow`. The
checkpoint lists the processed files, so a re-run ingests 0 rows, which is tested. On Databricks the same
role is played by Auto Loader.

**Q: How does gold avoid full recomputes?** Change Data Feed on silver. It reads changes since the last
processed version, finds the affected business dates, and `replaceWhere` rewrites only those dates. A test
adds one updated booking and asserts that only that date was recomputed.

**Q: Explain watermarks.** The watermark is the maximum event time seen, minus the allowed delay (30
minutes here). State for windows older than the watermark is finalised and dropped, and events older than
it are discarded. Subtlety I hit: with two stateful operators, Spark checks lateness against the
*previous* batch's watermark, so my test needed one extra delivery.

**Q: What does Delta give you over Parquet?** A transaction log: ACID commits, schema enforcement and
evolution, MERGE/UPDATE/DELETE, time travel, Change Data Feed, OPTIMIZE and Z-ORDER, VACUUM.

**Q: What surprised you?** `count()` after `VACUUM` still worked on an old version, because Delta answers
`count()` from statistics in the log without reading files. `collect()` proved the files were gone.

**Q: Why do the SQL and Spark pipelines agree?** The rules were written once per engine and tested;
then I reconciled gold per date and city: 0 differences, identical totals. A reconciliation check
runs every time.

### Databricks
**Q: Jobs vs DLT?** Jobs orchestrate tasks I write myself. DLT is declarative: I declare tables and
expectations, and the engine handles the dependency graph, incremental processing, retries and data-quality
metrics. I used jobs for the main flow with an explicit quality gate, and DLT for the declarative version
with SCD2.

**Q: How do you control cost and access on Databricks?** Job clusters, not all-purpose ones;
autoscaling; spot workers with an on-demand driver; serverless for small jobs; cluster policies capping
size and enforcing tags; a `run_as` service principal; Unity Catalog grants to groups; masks and row
filters; and the system billing tables for spend per job.

**Q: How do you deploy Databricks code?** An Asset Bundle in Git: `bundle validate` on pull requests,
`bundle deploy -t prod` from CI after approval. The bundle builds the wheel, so notebooks import tested
code.

### AWS / Terraform
**Q: How do you apply least privilege on S3?** Separate policies per role, scoped by bucket *and* prefix
(for example, the writer can only `PutObject` to `landing/*`), a TLS-only bucket policy, no ACLs, and
groups instead of per-user policies.

**Q: How do you avoid surprise bills?** Always run `terraform plan` before apply; an AWS Budget alerting
at 80% forecast and 100% actual; optional features off by default; lifecycle tiering; `default_tags` for
cost allocation; and `destroy` for dev.

**Q: What is an ExternalId for?** It prevents the "confused deputy" problem: Databricks can assume your
role only on behalf of your account.

### CI/CD and Git
**Q: What does your CI check?** Lint and format; unit tests on two Python versions; integration tests
against a real PostgreSQL that execute every SQL query; Spark tests; frontend lint, types, tests and
build; Terraform fmt and validate. Path filters and concurrency keep it fast.

**Q: How would you add CD safely?** Protected environments with required reviewers, GitHub OIDC
(no stored keys), `terraform plan` posted on PRs, apply on main after approval, and a bundle deploy with a
service principal.

### Monitoring and troubleshooting scenarios
**Q: The payments pipeline failed three times at 00:20. Walk me through it.** Open the run; read the error
(`DELTA_FAILED_TO_MERGE_FIELDS DOUBLE and STRING`); it's a schema change at the source, not transient, so
retries can't help. Downstream runs are cancelled, so nothing bad propagated. Inspect a landing file,
confirm with the source, quarantine or fix, re-run (checkpoints make that safe), verify gold for the
affected dates, then write an incident with follow-ups. That's INC-2026-001 and
`monitoring/runbooks/invalid-schema.md`.

**Q: The dashboard numbers look too high today.** Check for duplicate deliveries (`bookings_duplicate_deliveries`),
whether silver keys are still unique, and whether the same file was loaded twice under two names
(sha256 in the ingestion log). MERGE plus "latest wins" protects core; if duplicates reached core,
restore the Delta version and re-process.

**Q: The data is stale.** Check `stale_ingestion` and the ingestion log. Is the source late, or is a run
stuck (`long_running`)? Fix it, re-run, and the alert auto-resolves.

**Q: A metric you report is wrong. How would you even know?** By reconciling it against an independent
calculation. I found a real bug that way: a check counted reject *reasons* (4) instead of rows (813).
That's incident INC-2026-004.

### Behavioural
**Q: Tell me about a mistake.** The DQ check bug above, or the API error handlers: a positional
`JSONResponse(404, ...)` crashed every error handler, so every error became a bare 500. My smoke tests
of the error cases caught it. Lesson: test the failure paths, not just the happy path.

**Q: What would you do next?** Deploy to a Databricks workspace and AWS dev account, push to GitHub so CI
runs, add authentication to the API, use CUR data for real costs, and add data contracts with producers.

---

## Part 5 — Being honest about scope (say it before they ask)

- "The data is synthetic, and the cost data is simulated. Both are labelled in the data itself and in the UI."
- "The Databricks, AWS and hosting parts are written and validated locally (static checks, `terraform
  validate`, moto, local Docker deployment) but not yet run in real cloud accounts. The repo's status
  file labels exactly that."
- "Spark ran on an 8 GB laptop, so it's slow, but it ran the full dataset end to end and reconciled."
- "Lens is tested end to end locally and in CI, including real MySQL and MongoDB containers, but the hosted
  deployment is prepared, not performed."

This framing is a strength: it shows you know the difference between *written*, *tested* and *deployed*.

## Glossary (quick recall)

| Term | One-line meaning |
|---|---|
| Medallion | bronze (raw), silver (clean), gold (business-ready) layers |
| Idempotent | running it twice gives the same result as once |
| Watermark (SQL) | the high-water mark of what was already processed |
| Watermark (streaming) | max event time minus allowed lateness; closes windows |
| Checkpoint | stored progress + state of a streaming query |
| CDF | Delta Change Data Feed: row-level inserts/updates/deletes between versions |
| SCD1 / SCD2 | overwrite history / keep history with valid-from/valid-to |
| Sargable | a predicate an index can use (no function on the indexed column) |
| Quarantine | a table holding rejected rows plus the reason |
| ExternalId | protects an IAM role from the confused-deputy problem |
| OIDC | short-lived cloud credentials for CI without stored keys |
| RLS | Row-Level Security: Postgres filters rows by a policy, here the current workspace |
| Envelope encryption | encrypt data with a per-item key, then encrypt that key with a master key |
| SSRF | server-side request forgery: tricking a server into connecting somewhere internal |
| CSRF | cross-site request forgery; blocked by echoing a cookie value in a header |
| SKIP LOCKED | lets many workers claim different queue rows without blocking each other |
