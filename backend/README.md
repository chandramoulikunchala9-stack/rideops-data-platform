# Backend

The FastAPI application lives in the Python package (`src/rideops/api/`) so it shares configuration,
database code and SQL with the pipelines. This folder holds the **deployment** files.

| Task | Command |
|---|---|
| Run locally (reload) | `uvicorn rideops.api.main:app --reload` → http://localhost:8000/docs |
| Build image | `docker build -f backend/Dockerfile -t rideops-api .` (from the repo root) |
| Full stack in Docker | `docker compose --profile app up -d --build` → http://localhost:8080 |
| Tests | `pytest tests/api` (needs PostgreSQL) |

Environment variables: see `.env.example` (`POSTGRES_*`, `API_CORS_ORIGINS`, `RETRY_RUNNER_ENABLED`,
`MONTHLY_BUDGET_USD`, `ALERT_WEBHOOK_URL`). Hosted deployment: `docs/deployment.md`.
