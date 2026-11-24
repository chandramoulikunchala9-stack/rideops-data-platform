"""FastAPI application factory.

Run locally:   uvicorn rideops_api.main:app --reload
API docs:      http://localhost:8000/docs  (Swagger UI)  and  /redoc
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from rideops.config import ApiSettings, get_settings
from rideops.logging_config import configure_logging
from rideops_api import __version__, hardening
from rideops_api.auth import router as auth_router
from rideops_api.auth.deps import get_session
from rideops_api.background import Background
from rideops_api.db import Database
from rideops_api.errors import register_error_handlers
from rideops_api.jobs import tasks as _job_tasks  # noqa: F401 - registers job handlers
from rideops_api.lens.alerts import router as alerts_router
from rideops_api.lens.catalog import router as catalog_router
from rideops_api.lens.connections import router as connections_router
from rideops_api.lens.queries import router as queries_router
from rideops_api.notifications.email import EmailSender, make_sender
from rideops_api.routers import (
    analytics,
    costs,
    lens_system,
    monitoring,
    pipelines,
    quality,
    sources,
    system,
)
from rideops_api.runner import JobRunner
from rideops_api.settings import LensSettings, get_lens_settings
from rideops_api.workspaces import router as workspaces_router

logger = logging.getLogger("rideops_api")
API_PREFIX = "/api/v1"


def create_app(
    db: Database | None = None,
    runner: JobRunner | None = None,
    *,
    meta_db: Database | None = None,
    lens_settings: LensSettings | None = None,
    email_sender: EmailSender | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own dependencies; production creates them here."""
    configure_logging(get_settings().log_level)
    hardening.install_log_redaction()
    api_settings = ApiSettings()
    lens = lens_settings or get_lens_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = db or Database()
        app.state.runner = runner or JobRunner(enabled=api_settings.retry_runner_enabled)
        app.state.lens_settings = lens
        app.state.db.open()
        background = None
        if lens.lens_enabled:
            app.state.meta_db = meta_db or Database(conninfo=lens.metadata_conninfo())
            app.state.meta_db.open()  # no-op if the same pool is already open
            app.state.email_sender = email_sender or make_sender(lens.email_backend)
            if lens.lens_role == "all":
                background = Background(app.state.meta_db, lens, worker=True, scheduler=True)
                background.start()
        logger.info("RideOps API %s started (retry runner enabled=%s, lens=%s, role=%s)", __version__,
                    app.state.runner.enabled, lens.lens_enabled, lens.lens_role)  # fmt: skip
        yield
        if background:
            background.shutdown()
        app.state.runner.shutdown()
        # Close only pools this app created; injected ones (tests) belong to the caller.
        if lens.lens_enabled and meta_db is None and app.state.meta_db is not app.state.db:
            app.state.meta_db.close()
        if db is None:
            app.state.db.close()

    app = FastAPI(
        title="RideOps API",
        version=__version__,
        description="Analytics, pipeline monitoring, data quality and cost reporting for RideOps. "
        "All business data is synthetic; cost data is simulated unless labelled otherwise.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=api_settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
        allow_credentials=lens.lens_enabled,  # session cookie (Lens)
    )

    limiter = hardening.SlidingWindowLimiter(lens.rate_limit_per_minute)

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id[:64]
        started = time.perf_counter()
        path = request.url.path
        # §13.9 generic limit for Lens API routes (per session, else per IP); stricter limits live
        # in the endpoints themselves. Health and the internal tick are exempt.
        if (
            lens.lens_enabled
            and path.startswith(API_PREFIX)
            and not path.startswith(f"{API_PREFIX}/health")
        ):
            key = hardening.client_key(request.cookies.get("lens_session"),
                                       request.client.host if request.client else None)  # fmt: skip
            allowed, retry_after = limiter.hit(key)
            if not allowed:
                body = {
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": f"Too many requests; retry after {retry_after} s.",
                        "details": {"retry_after_s": retry_after},
                    }
                }
                response = JSONResponse(
                    status_code=429, content=body, headers={"Retry-After": str(retry_after)}
                )
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        route = request.scope.get("route")
        hardening.METRICS.observe(getattr(route, "path", "unmatched"), response.status_code)
        for name, value in hardening.security_headers(
            path, hsts=lens.session_cookie_secure and lens.lens_enabled
        ).items():
            response.headers.setdefault(name, value)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["x-request-id"] = request_id
        logger.info("%s %s %d %.1fms id=%s", request.method, request.url.path,
                    response.status_code, elapsed_ms, request_id)  # fmt: skip
        return response

    register_error_handlers(app)
    # Existing RideOps routers keep their paths (ADR-8). With Lens on and
    # LENS_PROTECT_RIDEOPS=true they require a signed-in session (migration step 2).
    legacy_deps = [Depends(get_session)] if lens.lens_enabled and lens.lens_protect_rideops else []
    app.include_router(system.router, prefix=API_PREFIX)  # /health stays public
    for module in (analytics, sources, pipelines, monitoring, costs, quality):
        app.include_router(module.router, prefix=API_PREFIX, dependencies=legacy_deps)
    if lens.lens_enabled:
        app.include_router(auth_router.router, prefix=API_PREFIX)
        app.include_router(workspaces_router.router, prefix=API_PREFIX)
        app.include_router(connections_router.router, prefix=API_PREFIX)
        app.include_router(connections_router.meta_router, prefix=API_PREFIX)
        app.include_router(catalog_router.router, prefix=API_PREFIX)
        app.include_router(queries_router.router, prefix=API_PREFIX)
        app.include_router(alerts_router.router, prefix=API_PREFIX)
        app.include_router(lens_system.router)  # /healthz, /readyz, /internal/tick at the root
    return app


app = create_app()


def run() -> None:
    """Console entry point `rideops-api`. Binds to localhost unless HOST/PORT say otherwise."""
    import os

    import uvicorn

    uvicorn.run(
        "rideops_api.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )
