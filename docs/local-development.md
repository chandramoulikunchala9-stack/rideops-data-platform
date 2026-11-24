# Local Development

New here? [How to run](how-to-run.md) is the short, step-by-step version for the backend and frontend.

## Prerequisites

| Tool | Version used | Needed for |
|---|---|---|
| Python | 3.10+ (CI also tests 3.12) | everything Python |
| Docker Desktop | 29.x | PostgreSQL, Spark runtime, Terraform/actionlint checks, app stack |
| Node.js / npm | 22+ / 10+ | dashboard |
| Java 17/21 | only if you run Spark *outside* Docker | – |

8 GB of RAM works but is tight for Spark. Close heavy apps while running `rideops.spark`.

## First-time setup

```bash
py -3 -m venv .venv && .venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -e "./pipelines[dev,aws]" -e "./backend[dev,engines]"   # engines = MySQL, SQL Server, MongoDB drivers for Lens
copy .env.example .env                               # set POSTGRES_PASSWORD (any random string)
docker compose up -d postgres
cd frontend && npm ci && cd ..
```

## Daily workflow

```bash
python -m rideops.generators --scale small       # or full (100k bookings, ~30 s)
python -m rideops.db all                         # incremental: re-runs only process new files
python -m rideops.monitoring seed-incidents
python -m rideops.monitoring alerts
uvicorn rideops_api.main:app --reload            # http://localhost:8000/docs
cd frontend && npm run dev                       # http://localhost:5173 landing, /app/ dashboard (proxies /api to :8000)
```

Spark / Delta Lake (Docker):

```bash
docker compose --profile spark build spark
docker compose --profile spark run --rm spark python -m rideops.spark all
docker compose --profile spark run --rm spark python -m rideops.spark delta-demo
docker compose --profile spark run --rm spark python -m rideops.spark stream --live   # with: replay
```

## Tests and checks

| What | Command |
|---|---|
| Python lint + format | `ruff check pipelines backend scripts && ruff format --check pipelines backend scripts` |
| Fast pipeline unit tests | `pytest pipelines -m "not slow and not postgres and not spark"` |
| Pipelines incl. PostgreSQL | `pytest pipelines -m "not spark"` |
| Backend API incl. Lens | `pytest backend` (needs PostgreSQL) |
| Lens only | `pytest backend/tests/lens` |
| Spark | `docker compose --profile spark run --rm spark pytest pipelines/tests/spark` |
| Frontend | `cd frontend && npm run lint && npm run typecheck && npm test && npm run build` |
| Terraform | see `docs/aws-setup.md` (runs in Docker) |
| Workflows | `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12` |

## Working on Lens

Enable it as described in [how to run § Lens](how-to-run.md#lens) (`LENS_ENABLED=true`, a `LENS_MASTER_KEYS`
key, `python -m rideops_api migrate`). Useful pieces:

* **Schema changes** go in a new Alembic revision under `backend/src/rideops_api/migrations/versions/`
  (`0006_…py`); `pytest backend/tests/lens/test_jobs.py` checks that the chain upgrades and downgrades.
* **Tests** create a throwaway database `rideops_lens_test` and migrate it to head. Tests that connect Lens to a
  database use the same server, with `sslmode` matching whether it has TLS on.
* **Other engines**: the adapter contract suite runs against any of these that is reachable (CI starts MySQL and
  MongoDB; SQL Server is local only):

  ```bash
  docker run -d --name lens-mysql -e MYSQL_ROOT_PASSWORD=RootPass-123 -e MYSQL_DATABASE=shop -p 3307:3306 mysql:8.4
  docker run -d --name lens-mongo -e MONGO_INITDB_ROOT_USERNAME=root -e MONGO_INITDB_ROOT_PASSWORD=RootPass-123 -p 27018:27017 mongo:8.0
  docker run -d --name lens-mssql -e ACCEPT_EULA=Y -e "MSSQL_SA_PASSWORD=SaPass-12345!" -p 1434:1433 mcr.microsoft.com/mssql/server:2022-latest
  ```

* **Emails** print to the API log with `EMAIL_BACKEND=console`.
* **Background work**: with `LENS_ROLE=all` the API process runs the worker and scheduler threads. To debug
  one job type, run `python -m rideops_api serve --role api` and `python -m rideops_api worker` in two terminals.
* **New engine**: implement the adapter interface in `lens/adapters/base.py`, register it in
  `lens/adapters/registry.py` and add it to the contract suite in `backend/tests/lens/test_adapter_contract.py`.

## Practise an incident

```bash
python -m rideops.monitoring simulate-incident   # drops a schema-drifted bookings file
python -m rideops.db all                         # loader rejects it; landing_schema_mismatch FAILs
python -m rideops.monitoring alerts              # data_quality_failed alert fires
# follow monitoring/runbooks/invalid-schema.md, then:
python -m rideops.monitoring resolve-incident
python -m rideops.db quality && python -m rideops.monitoring alerts   # alert auto-resolves
```

## Project layout

See the README's *Repository layout*. Each sub-package of `rideops` has a `__main__.py` CLI with `--help`.
Run all commands from the **repository root** (that is where `.env` and `data/` live).
