"""Health check and settings/access information."""

import platform
from datetime import datetime, timezone
from importlib import metadata

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from rideops.config import PostgresSettings, get_settings
from rideops_api import __version__, schemas
from rideops_api.governance import DEMO_USERS, ROLES
from rideops_api.params import DbDep
from rideops_api.routers.monitoring import SettingsDep

router = APIRouter(tags=["system"])


@router.get("/health", response_model=schemas.Health, responses={503: {"model": schemas.Health}})
def health(request: Request, db: DbDep) -> JSONResponse:
    """Liveness + database readiness. Returns 503 when the database is unreachable,
    so a load balancer or hosting platform stops routing traffic here."""
    try:
        db.scalar("SELECT 1")
        database = "ok"
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        database = "unavailable"
    body = schemas.Health(
        status="ok" if database == "ok" else "degraded",
        database=database,
        version=__version__,
        environment=get_settings().env,
        retry_runner_enabled=request.app.state.runner.enabled,
        time=datetime.now(timezone.utc),
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if database == "ok" else 503)


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "not installed here (runs in the spark container / Databricks)"


@router.get("/settings/access", response_model=schemas.AccessSettings)
def access(request: Request, db: DbDep, settings: SettingsDep) -> schemas.AccessSettings:
    pg = PostgresSettings()
    app_settings = get_settings()
    window = db.fetch_one(
        "SELECT min(booking_date) AS first, max(booking_date) AS last FROM core.bookings"
    )
    return schemas.AccessSettings(
        roles=[schemas.Role(**r) for r in ROLES],
        users=[schemas.DemoUser(**u) for u in DEMO_USERS],
        users_note="Fictional demo users. In production, users come from the identity provider (SCIM).",
        environment={
            "environment": app_settings.env,
            "app_version": __version__,
            "python": platform.python_version(),
            "fastapi": _version("fastapi"),
            "pyspark": _version("pyspark"),
            "delta_spark": _version("delta-spark"),
            "database": f"postgresql://{pg.host}:{pg.port}/{pg.db}",  # never the password
            "storage_backend": "local filesystem (data/)",
            "data_window": f"{window['first']} to {window['last']}" if window else None,
            "retry_runner_enabled": request.app.state.runner.enabled,
            "monthly_budget_usd": settings.monthly_budget_usd,
            "cost_data": "simulated (see docs/cost-management.md for real AWS/Databricks sources)",
        },
    )
