# Deployment

> **Verification status (2026-11-19)**
> * **Local production-style deployment (Docker): verified.** `scripts/verify_deployment.py` → 12/12 checks
>   passed against `docker compose --profile app` (API container healthy, nginx serving the SPA, `/api` proxy).
> * **Hosted deployment: prepared, not yet performed.** It needs your Cloudflare, Render and Neon accounts.
>   The steps below are ready to follow. Run the verification script afterwards before calling it deployed.

## 1. Local deployment (production-style, Docker)

```bash
cp .env.example .env                       # set POSTGRES_PASSWORD
docker compose up -d postgres
python -m rideops.generators --scale full
python -m rideops.db all                   # migrate, load, transform, analytics, DQ checks
python -m rideops.monitoring seed-incidents && python -m rideops.monitoring alerts
docker compose --profile app up -d --build # API (8001) + dashboard behind nginx (8080)
python scripts/verify_deployment.py --api http://localhost:8001 --web http://localhost:8080
```

Open http://localhost:8080 (landing page) and http://localhost:8080/app/ (dashboard). API docs:
http://localhost:8001/docs.

Lakehouse (optional): `docker compose --profile spark run --rm spark python -m rideops.spark all`.

## 2. Hosted deployment (free tiers)

Free-tier limits were checked on 2026-11-19 from the providers' docs (verify again before relying on them):

| Part | Provider | Free-tier facts that shaped the choice |
|---|---|---|
| Dashboard (static) | **Cloudflare Pages** | 500 builds/month, 1 concurrent build, 20-minute build timeout; static requests and bandwidth not metered |
| API (Docker) | **Render** web service | 750 instance-hours/month; **spins down after 15 min idle, about 1 min to wake**; ephemeral filesystem |
| Database | **Neon** Postgres | 1 GB storage per project, 100 CU-hours/month, scales to zero. Chosen over Render's free Postgres, which **expires after 30 days** |

Because of Neon's 1 GB limit, the hosted demo uses the `small` dataset (10k bookings). The `full`
dataset stays local.

### Step 1: database (Neon)

1. Create a Neon project (closest region) and copy the connection details.
2. From your machine, build the database there (same code as local):
   ```bash
   # PowerShell: $env:POSTGRES_HOST="ep-xxx.<region>.aws.neon.tech"; ...
   export POSTGRES_HOST=ep-xxx.<region>.aws.neon.tech POSTGRES_DB=neondb POSTGRES_USER=<user> \
          POSTGRES_PASSWORD=<password> POSTGRES_SSLMODE=require
   python -m rideops.generators --scale small --data-dir data-hosted
   RIDEOPS_DATA_DIR=data-hosted python -m rideops.db all
   python -m rideops.monitoring seed-incidents && python -m rideops.monitoring alerts
   ```

### Step 2: API (Render)

1. Push the repository to GitHub.
2. Render → **New → Blueprint** → select the repo. `render.yaml` defines the service (Docker,
   `healthCheckPath: /api/v1/health`, `autoDeploy: false`).
3. Enter the secrets Render asks for (`POSTGRES_*`, and `API_CORS_ORIGINS` = your Pages URL).
   `RETRY_RUNNER_ENABLED` stays `false` in public: no job execution from the internet.
4. Deploy, then open `https://<service>.onrender.com/api/v1/health`. Expect `{"status":"ok","database":"ok"}`.

### Step 3: dashboard (Cloudflare Pages)

1. Cloudflare → Workers & Pages → **Create → Pages → Connect to Git** → the repo.
2. Build settings: root directory `frontend`, build command `npm ci && npm run build`, output `dist`.
3. Environment variables: `API_ORIGIN=https://<service>.onrender.com` and leave `VITE_API_BASE_URL`
   empty. `frontend/functions/api/[[path]].js` (a Pages Function) proxies `/api/*` to Render, so the app
   calls its own origin; this is required for Lens sign-in (see the Lens section below). Without Lens,
   `VITE_API_BASE_URL=https://<service>.onrender.com/api/v1` (baked in at build time) also works.
4. `frontend/public/_redirects` sends client-side routes such as `/pipelines/x` to `index.html`.

### Step 4: verify

```bash
python scripts/verify_deployment.py --api https://<service>.onrender.com --web https://<project>.pages.dev
```

All checks must pass, and the dashboard must load data, before the deployment counts as done. Record the
result in `PROJECT_STATUS.md`.

## Lens (multi-database monitoring) — hosted setup

Lens runs inside the same API service. **Status: prepared and verified locally (18/18 checks with
`--lens`), not deployed** — it needs your Render, Neon and Cloudflare accounts.

1. **Metadata database (Neon).** Use the same Neon database (schema `app`) or a separate one via
   `DATABASE_URL`. Migrations run automatically at start-up when `LENS_ENABLED=true`
   (`python -m rideops_api migrate` by hand).
2. **Demo connection.** Create a read-only role for the RideOps data only — never the `app` schema:
   ```sql
   CREATE ROLE rideops_demo_ro LOGIN PASSWORD '<strong password>' CONNECTION LIMIT 5;
   GRANT CONNECT ON DATABASE <db> TO rideops_demo_ro;
   GRANT USAGE ON SCHEMA raw, core, analytics, ops TO rideops_demo_ro;
   GRANT SELECT ON ALL TABLES IN SCHEMA raw, core, analytics, ops TO rideops_demo_ro;
   ALTER ROLE rideops_demo_ro SET default_transaction_read_only = on;
   ```
   and set `RIDEOPS_DEMO_URL=postgresql://rideops_demo_ro:<password>@<host>/<db>?sslmode=require`.
3. **Render.** The blueprint (`render.yaml`) already lists the Lens variables. Fill the secret ones:
   `LENS_MASTER_KEYS` (generate with
   `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"` and keep a copy in
   your password manager — losing it means re-entering every connection password),
   `INTERNAL_TOKEN` (random), `PUBLIC_APP_URL` (your Pages URL), `DATABASE_URL` if separate.
4. **Cloudflare Pages.** Leave `VITE_API_BASE_URL` **empty** and set `API_ORIGIN` to the Render URL.
   `frontend/functions/api/[[path]].js` proxies `/api/*` to Render so the dashboard and API share one
   origin — required because Lens's session cookie is `SameSite=Lax` and browsers don't send it from
   `*.pages.dev` to `*.onrender.com`.
5. **Keep checks running.** Free Render instances sleep. Add repository secrets `LENS_API_URL` and
   `LENS_INTERNAL_TOKEN`; the `lens-tick` workflow then calls `POST /internal/tick` every 10 minutes.
6. **Verify.**
   ```bash
   python scripts/verify_deployment.py --api https://<service>.onrender.com --web https://<project>.pages.dev --lens
   ```
7. **Monitoring.** Scrape `GET /metrics` with `Authorization: Bearer $INTERNAL_TOKEN`. Runbooks:
   `monitoring/runbooks/lens-*.md`.

Known free-tier limits: no persistent disk (SQLite uploads are lost on restart; set `UPLOADS_DIR`
to a mounted disk or disable uploads), no static egress IPs (firewalled databases can't allowlist
Lens), emails are logged rather than sent until an email provider is configured.

## Environment variables

| Variable | Where | Notes |
|---|---|---|
| `POSTGRES_HOST/PORT/DB/USER/PASSWORD` | API, CLIs | Password has no default. Secret in hosting dashboards |
| `POSTGRES_SSLMODE` | API, CLIs | `require` for Neon |
| `API_CORS_ORIGINS` | API | Comma-separated frontend origins |
| `RETRY_RUNNER_ENABLED` | API | `true` only on trusted/local deployments |
| `MONTHLY_BUDGET_USD` | API, alerts | Budget for cost warnings |
| `ALERT_WEBHOOK_URL` | alerts | Optional Slack/Teams incoming webhook (secret) |
| `VITE_API_BASE_URL` | frontend build | Absolute API URL when the frontend is hosted separately |
| `RIDEOPS_DATA_DIR`, `RIDEOPS_SCALE`, `RIDEOPS_SEED` | CLIs | Data generation and loading |

## Health checks

* API: `GET /api/v1/health` returns **200** when the database is reachable and **503** otherwise, so
  load balancers stop routing to an instance that cannot serve data. The Docker image has a `HEALTHCHECK`,
  and Render uses `healthCheckPath`.
* Frontend: the sidebar shows API status. `verify_deployment.py` checks the deep-link fallback.

## Rollback

* Render: Deploys → redeploy the previous successful deploy.
* Cloudflare Pages: Deployments → roll back to a previous build.
* Database: migrations only add objects (`CREATE ... IF NOT EXISTS`). Neon branches give point-in-time
  copies before risky changes.
