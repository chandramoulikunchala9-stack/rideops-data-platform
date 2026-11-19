# backend/ — RideOps REST API (Python package `rideops_api`)

FastAPI service that serves analytics, pipeline monitoring, data quality, alerts/incidents and cost
reporting to the dashboard. It reads PostgreSQL and reuses the `rideops` library from `../pipelines`
(settings, pipeline runner, alert rules).

```
backend/
├── src/rideops_api/
│   ├── main.py          app factory: middleware, error handlers, routers, lifespan
│   ├── db.py            psycopg connection pool + query helpers
│   ├── errors.py        uniform JSON errors {"error": {code, message, details}}
│   ├── params.py        reusable query parameters (date range, pagination)
│   ├── schemas.py       Pydantic response models (drive the OpenAPI docs)
│   ├── runner.py        whitelisted job runner behind POST /pipelines/{name}/retry
│   ├── governance.py    roles & permissions shown on the Settings page
│   └── routers/         analytics, pipelines, monitoring, costs, quality, system
├── tests/               27 API tests against a throwaway PostgreSQL database
├── Dockerfile           production image (non-root, health check, $PORT)
└── pyproject.toml
```

## Commands (from the repository root)

| Task | Command |
|---|---|
| Install | `pip install -e ./pipelines -e "./backend[dev]"` |
| Run with reload | `uvicorn rideops_api.main:app --reload` → http://localhost:8000/docs |
| Run (entry point) | `rideops-api` |
| Tests | `pytest backend` (needs PostgreSQL; skipped otherwise) |
| Image | `docker build -f backend/Dockerfile -t rideops-api .` |
| Full stack | `docker compose --profile app up -d --build` → http://localhost:8080 |

Environment variables: see `../.env.example`. Hosting: `../docs/deployment.md`.
