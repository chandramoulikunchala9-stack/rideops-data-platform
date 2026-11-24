# How to run RideOps (backend and frontend)

This guide starts the whole app on your machine: the PostgreSQL database, the FastAPI backend, the
landing page and the dashboard, and optionally **RideOps Lens** (sign-in, connect your own databases;
see [Lens](#lens)). Commands are for **Windows PowerShell**; where macOS or Linux
differs, the alternative is given in a comment.

Run every command from the **repository root** (the folder with `docker-compose.yml`) unless the
step says `cd frontend`.

There are two ways to run it:

| | Option A: developer mode | Option B: everything in Docker |
|---|---|---|
| Use it when | you are changing code (hot reload) | you just want to see the app |
| Needs | Python, Node.js, Docker | Docker only (plus Python to create the data once) |
| Landing page | http://localhost:5173 | http://localhost:8080 |
| Dashboard | http://localhost:5173/app/ | http://localhost:8080/app/ |
| API docs | http://localhost:8000/docs | http://localhost:8001/docs |

---

## 1. Prerequisites

| Tool | Version | Check with |
|---|---|---|
| Python | 3.10 or newer | `python --version` |
| Node.js and npm | Node 22+, npm 10+ | `node --version` and `npm --version` |
| Docker Desktop | running | `docker version` |
| Git | any | `git --version` |

## 2. One-time setup

```powershell
git clone https://github.com/chandramoulikunchala9-stack/rideops-data-platform.git
cd rideops-data-platform

# Python virtual environment
py -3 -m venv .venv                      # macOS/Linux: python3 -m venv .venv
.\.venv\Scripts\Activate.ps1             # macOS/Linux: source .venv/bin/activate

# Install the pipelines library and the backend (editable, with dev tools)
pip install -e "./pipelines[dev,aws]" -e "./backend[dev]"

# Settings file (git-ignored, never commit it)
Copy-Item .env.example .env              # macOS/Linux: cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

Open `.env` and paste the printed value after `POSTGRES_PASSWORD=`. Everything else in `.env` can stay
as it is.

> If PowerShell refuses to run `Activate.ps1` ("running scripts is disabled"), run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again.

## 3. Start the database and load the data

```powershell
docker compose up -d postgres                 # PostgreSQL 17 on localhost:5432
docker compose ps                             # wait until postgres shows "healthy"

python -m rideops.generators --scale full     # synthetic data: 100,000 bookings (about 30 s)
python -m rideops.db all                      # migrate -> load -> transform -> quality checks
python -m rideops.monitoring seed-incidents   # incident postmortems into the database
python -m rideops.monitoring alerts           # evaluate the alert rules
```

Use `--scale small` instead of `full` for a quicker, smaller dataset. Re-running `python -m rideops.db all`
is safe: it only processes new files.

---

## Option A: developer mode (two terminals)

### Terminal 1: backend (FastAPI)

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn rideops_api.main:app --reload
```

- API: http://localhost:8000/api/v1
- Interactive docs: http://localhost:8000/docs
- `--reload` restarts the server when you save a Python file.
- `rideops-api` does the same without reload (it listens on 127.0.0.1:8000; set `HOST`/`PORT` to change).

### Terminal 2: frontend (landing page and dashboard)

```powershell
cd frontend
npm ci              # first time only, or after package-lock.json changes
npm run dev
```

- Landing page: http://localhost:5173
- Dashboard: http://localhost:5173/app/

The dev server forwards every `/api` request to the backend on port 8000, so no extra frontend
configuration is needed. Keep Terminal 1 running, or the dashboard shows "Could not load data".

### Stop

Press `Ctrl+C` in both terminals, then `docker compose stop postgres`.

---

## Option B: everything in Docker

Do steps 2 and 3 once (they create `.env` and the data), then:

```powershell
docker compose --profile app up -d --build
```

| Service | URL |
|---|---|
| Landing page | http://localhost:8080 |
| Dashboard | http://localhost:8080/app/ |
| API (direct) | http://localhost:8001/docs |

The web container (nginx) serves the built frontend and forwards `/api` to the API container.
Rebuild with the same command after code changes.

### Stop

```powershell
docker compose --profile app down        # keeps the database volume
docker compose --profile app down -v     # also deletes the database (reload with step 3)
```

---

## 4. Check that it works

```powershell
# Developer mode
python scripts/verify_deployment.py --api http://localhost:8000 --web http://localhost:5173

# Docker
python scripts/verify_deployment.py --api http://localhost:8001 --web http://localhost:8080
```

All 13 checks should print `PASS`: API health, database, the main endpoints, the landing page at `/`,
the dashboard at `/app/`, a dashboard deep link, and the `/api` forwarding. A quick manual check:

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/health    # macOS/Linux: curl http://localhost:8000/api/v1/health
```

## 5. Run the tests

| What | Command |
|---|---|
| Pipelines (needs Postgres running) | `pytest pipelines -m "not spark"` |
| Backend API (needs Postgres running) | `pytest backend` |
| Frontend | `cd frontend; npm run lint; npm run typecheck; npm test; npm run build` |
| Python lint | `ruff check pipelines backend scripts` |
| Lens only | `pytest backend/tests/lens` (the engine contract tests run for each of MySQL `:3307`, MongoDB `:27018` and SQL Server `:1434` that is reachable; see `.github/workflows/python.yml` for the containers) |

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| Dashboard says "Could not load data" | The backend is not running. Start Terminal 1 and check http://localhost:8000/api/v1/health. |
| `password authentication failed` | You changed `POSTGRES_PASSWORD` after the database was first created. Put the old value back, or reset with `docker compose down -v` and repeat step 3 (this deletes the data). |
| `port is already allocated` / address in use | Another program uses 5432, 8000, 5173 or 8080. Stop it, or set `POSTGRES_PORT` in `.env` for the database. |
| `npm ci` fails with `EPERM ... unlink ... rolldown-binding.win32-x64-msvc.node` | A running Vite server (`npm run dev` or `vite preview`) still has that file open, and `npm ci` deletes `node_modules` first. Stop every Vite server (`Ctrl+C`, or `Get-CimInstance Win32_Process -Filter "Name='node.exe'"` to find it, then `Stop-Process -Id <id>`), then run `npm ci` again. If you skip that, `npm run dev` then fails with "'vite' is not recognized". |
| `ModuleNotFoundError: rideops` | The virtual environment is not active, or step 2's `pip install` was skipped. |
| Dashboard pages are empty | Step 3 was not run, or `python -m rideops.db all` failed. Run it again and read its output. |
| Vitest says "failed to find the current suite" in Git Bash on Windows | Run `npm test` from PowerShell. Git Bash passes the drive letter in lower case, which loads Vitest twice. |
| `/app/some-page` shows 404 in production | The host needs the SPA rule: nginx's `location /app/` (in `frontend/nginx.conf`) or Cloudflare's `frontend/public/_redirects`. |

## Lens

Lens adds accounts, workspaces and monitoring of any database you connect. It is off until you enable it.

### 1. Configure `.env`

PostgreSQL connections work with the packages from step 2. To connect MySQL, SQL Server or MongoDB too,
install the extra drivers (the Docker image already has them):

```powershell
pip install -e "./backend[engines]"
```

Generate the master key:

```powershell
python -c "import os,base64;print('1:'+base64.b64encode(os.urandom(32)).decode())"
```

Add (or change) these lines in `.env`, pasting the printed key:

```ini
LENS_ENABLED=true
LENS_MASTER_KEYS=1:<printed key>         # encrypts saved connection passwords; keep a copy
SESSION_COOKIE_SECURE=false              # plain http on localhost
PUBLIC_APP_URL=http://localhost:5173     # Option B: http://localhost:8080
ALLOW_PRIVATE_NETWORKS=true              # lets you connect databases on localhost / your LAN
EMAIL_BACKEND=console                    # verification and reset links are printed in the API log
```

### 2. Optional: the RideOps demo connection

Every new workspace can get a read-only "RideOps demo" connection to the warehouse you loaded in step 3.
Create a read-only role once (`docker exec -it rideops-postgres psql -U rideops -d rideops`):

```sql
CREATE ROLE rideops_demo_ro LOGIN PASSWORD 'demo-ro-local-only' CONNECTION LIMIT 5;
GRANT CONNECT ON DATABASE rideops TO rideops_demo_ro;
GRANT USAGE ON SCHEMA raw, core, analytics, ops TO rideops_demo_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA raw, core, analytics, ops TO rideops_demo_ro;
ALTER ROLE rideops_demo_ro SET default_transaction_read_only = on;
```

and add to `.env` (use host `postgres` instead of `localhost` for Option B):

```ini
RIDEOPS_DEMO_URL=postgresql://rideops_demo_ro:demo-ro-local-only@localhost:5432/rideops?sslmode=disable
```

### 3. Start and sign up

Option A: create the Lens tables once (schema `app`; re-run after pulling new migrations), then restart the backend:

```powershell
python -m rideops_api migrate
uvicorn rideops_api.main:app --reload
```

Option B: run the compose command again; the API container applies the migrations itself at start-up.
Either way the API starts the Lens worker and scheduler in the same process (`LENS_ROLE=all`). Then:

1. Open http://localhost:5173/app/signup (Option B: http://localhost:8080/app/signup) and create an account.
2. Copy the verification link from the API log (`Verify your RideOps Lens email`) into the browser.
3. You land on the **fleet overview** at `/app/lens`. Click **Add connection**, paste a database URL such as
   `postgresql://user@localhost:5432/mydb` (or `mysql://…`, `sqlserver://…`, `mongodb://…`, or upload a
   SQLite file), enter the password and run the test. Each stage shows what passed or failed.
4. Save. The first health check runs within a minute; open the connection for status, schema, profiling,
   insights, the query console, dashboards and alerts.

| Command | What |
|---|---|
| `python -m rideops_api migrate` | apply the Lens migrations by hand |
| `python -m rideops_api serve --role api` | API without the background threads |
| `python -m rideops_api worker` / `scheduler` | run the worker or scheduler as separate processes |
| `python -m rideops_api rotate-keys` | re-encrypt saved secrets after adding a new `2:<key>` to `LENS_MASTER_KEYS` |
| `python scripts/verify_deployment.py --api http://localhost:8000 --lens` | 9 extra Lens checks (sign-up, CSRF, demo health, SQL guard, SSRF block) |

Lens troubleshooting:

| Problem | Fix |
|---|---|
| `/readyz` returns 503 with an error under `keys` | `LENS_MASTER_KEYS` is empty or not `1:<base64 of 32 bytes>` |
| `/readyz` shows `migrations: at …, expected …` or API errors mention `app.` tables | Run `python -m rideops_api migrate` |
| Sign-in succeeds but you are sent back to the login page | The browser dropped the session cookie: set `SESSION_COOKIE_SECURE=false` for plain http |
| Connection test stops at `NETWORK_POLICY` (`CONN_BLOCKED_NETWORK`) | The host is private or loopback. Set `ALLOW_PRIVATE_NETWORKS=true` locally, or add the host to the workspace's egress allowlist |
| Test stops at `TLS` with `CONN_TLS_REQUIRED` | The URL asks for `sslmode=disable`; a workspace owner must allow insecure connections in workspace settings |
| No verification email | Expected with `EMAIL_BACKEND=console`: the link is in the API log |

## Ports at a glance

| Port | What |
|---|---|
| 5432 | PostgreSQL (Docker) |
| 8000 | FastAPI backend, developer mode |
| 5173 | Vite dev server: landing page and dashboard |
| 8001 | FastAPI backend in Docker |
| 8080 | nginx in Docker: landing page and dashboard (Lens at `/app/lens`) |

More detail: [local development](local-development.md) (Spark, incident drill), [deployment](deployment.md)
(hosted setup) and [troubleshooting](troubleshooting.md).
