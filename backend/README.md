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
│   ├── routers/         analytics, pipelines, monitoring, costs, quality, system, lens_system
│   ├── settings.py      Lens settings (LENS_* and friends, see ../.env.example)
│   ├── migrate.py       Alembic wrapper; migrations/ holds the `app` schema revisions
│   ├── auth/            Lens sign-up/login, server-side sessions, CSRF, Argon2id passwords
│   ├── workspaces/      workspaces, members, invitations, RBAC matrix, audit log API
│   ├── jobs/            Postgres job queue (SKIP LOCKED), worker, scheduler, job handlers
│   ├── notifications/   outgoing email (console / in-memory in Phase 11)
│   ├── audit.py         audit-log writer
│   └── background.py    in-process worker + scheduler threads for LENS_ROLE=all
├── tests/               API tests (throwaway PostgreSQL); tests/lens/ covers Lens
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

## Lens (Phase 11, opt-in)

Lens turns RideOps into a multi-tenant "connect any database" product. Design:
`../docs/ReContruction/lens-system-design.md`. It is **off by default**; nothing changes for the
current dashboard until you set `LENS_ENABLED=true`.

| Task | Command |
|---|---|
| Apply metadata migrations (schema `app`) | `python -m rideops_api migrate` |
| API + worker + scheduler in one process | `LENS_ENABLED=true python -m rideops_api serve --role all` |
| API only / worker only / scheduler only | `serve --role api` · `python -m rideops_api worker` · `python -m rideops_api scheduler` |
| Lens tests only | `pytest backend/tests/lens` |

Endpoints when enabled: `/api/v1/auth/*` (signup, verify-email, login, logout, password
forgot/reset, me), `/api/v1/workspaces/*` (CRUD, members, invitations, audit),
`/api/v1/invitations/{token}/accept`, and `/healthz`, `/readyz`, `POST /internal/tick` at the root.
Mutating requests need the `X-CSRF-Token` header (value from `/auth/me` or the `lens_csrf` cookie).
In local development set `SESSION_COOKIE_SECURE=false` (plain http) and read verification/reset links
from the API log (`EMAIL_BACKEND=console`).

Environment variables: see `../.env.example`. Hosting: `../docs/deployment.md`.
