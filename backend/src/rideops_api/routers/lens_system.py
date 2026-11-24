"""Lens system endpoints (§13.8): liveness, readiness, and the internal scheduler tick."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from rideops_api import migrate
from rideops_api.auth.deps import LensSettingsDep, MetaDbDep
from rideops_api.auth.tokens import same
from rideops_api.errors import ApiError
from rideops_api.jobs import scheduler
from rideops_api.jobs.worker import Worker
from rideops_api.lens.connections.service import keyring_for
from rideops_api.lens.security.crypto import KeyRingError

router = APIRouter(tags=["system"])
TICK_MAX_JOBS = 20


@router.get("/healthz")
def healthz() -> dict:
    """Liveness: the process is up. Never touches the database."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(db: MetaDbDep, settings: LensSettingsDep) -> JSONResponse:
    """Readiness: metadata DB reachable, migrated to head, and the encryption key ring loaded."""
    checks: dict[str, str] = {}
    try:
        with db.pool.connection() as conn:
            current = migrate.current_revision(conn)
        head = migrate.head_revision()
        checks["database"] = "ok"
        checks["migrations"] = "ok" if current == head else f"at {current}, expected {head}"
    except Exception:  # noqa: BLE001 - any failure means not ready
        checks["database"] = "unavailable"
        checks["migrations"] = "unknown"
    try:
        keyring_for(settings)
        checks["keys"] = "ok"
    except KeyRingError as exc:
        checks["keys"] = str(exc)
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ready" if ready else "not_ready", "checks": checks},
                        status_code=200 if ready else 503)  # fmt: skip


@router.post("/internal/tick")
def internal_tick(request: Request, db: MetaDbDep, settings: LensSettingsDep) -> dict:
    """Wake a sleeping free-tier instance: run one scheduler tick and drain a few jobs.
    Called by an external cron with `X-Internal-Token`. Disabled when INTERNAL_TOKEN is unset."""
    token = settings.internal_token.get_secret_value() if settings.internal_token else ""
    given = request.headers.get("x-internal-token", "")
    if not token:
        raise ApiError(404, "NOT_FOUND", "Not found.")
    if not given or not same(given, token):
        raise ApiError(403, "FORBIDDEN", "Invalid internal token.")
    result = scheduler.tick(db)
    ran = Worker(db, settings=settings).drain(TICK_MAX_JOBS)
    return {
        "leader": result.leader,
        "enqueued": result.enqueued,
        "reaped": result.reaped,
        "jobs_run": ran,
    }
