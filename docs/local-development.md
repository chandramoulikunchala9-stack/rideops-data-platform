# Local Development

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
pip install -e "./pipelines[dev,aws]" -e "./backend[dev]"
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
cd frontend && npm run dev                       # http://localhost:5173 (proxies /api to :8000)
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
| Backend API | `pytest backend` (needs PostgreSQL) |
| Spark | `docker compose --profile spark run --rm spark pytest pipelines/tests/spark` |
| Frontend | `cd frontend && npm run lint && npm run typecheck && npm test && npm run build` |
| Terraform | see `docs/aws-setup.md` (runs in Docker) |
| Workflows | `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12` |

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
