# How to run RideOps (backend and frontend)

This guide starts the whole app on your machine: the PostgreSQL database, the FastAPI backend, the
landing page and the dashboard. Commands are for **Windows PowerShell**; where macOS or Linux
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

## Ports at a glance

| Port | What |
|---|---|
| 5432 | PostgreSQL (Docker) |
| 8000 | FastAPI backend, developer mode |
| 5173 | Vite dev server: landing page and dashboard |
| 8001 | FastAPI backend in Docker |
| 8080 | nginx in Docker: landing page and dashboard |

More detail: [local development](local-development.md) (Spark, incident drill), [deployment](deployment.md)
(hosted setup) and [troubleshooting](troubleshooting.md).
