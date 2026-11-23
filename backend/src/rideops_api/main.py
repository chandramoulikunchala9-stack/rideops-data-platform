"""FastAPI application factory.

Run locally:   uvicorn rideops_api.main:app --reload
API docs:      http://localhost:8000/docs  (Swagger UI)  and  /redoc
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from rideops.config import ApiSettings, get_settings
from rideops.logging_config import configure_logging
from rideops_api import __version__
from rideops_api.db import Database
from rideops_api.errors import register_error_handlers
from rideops_api.routers import analytics, costs, monitoring, pipelines, quality, sources, system
from rideops_api.runner import JobRunner

logger = logging.getLogger("rideops_api")
API_PREFIX = "/api/v1"


def create_app(db: Database | None = None, runner: JobRunner | None = None) -> FastAPI:
    """Build the app. Tests pass their own Database/JobRunner; production creates them here."""
    configure_logging(get_settings().log_level)
    api_settings = ApiSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = db or Database()
        app.state.runner = runner or JobRunner(enabled=api_settings.retry_runner_enabled)
        app.state.db.open()
        logger.info("RideOps API %s started (retry runner enabled=%s)", __version__,
                    app.state.runner.enabled)  # fmt: skip
        yield
        app.state.runner.shutdown()
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
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["x-request-id"] = request_id
        logger.info("%s %s %d %.1fms id=%s", request.method, request.url.path,
                    response.status_code, elapsed_ms, request_id)  # fmt: skip
        return response

    register_error_handlers(app)
    for module in (system, analytics, sources, pipelines, monitoring, costs, quality):
        app.include_router(module.router, prefix=API_PREFIX)
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
