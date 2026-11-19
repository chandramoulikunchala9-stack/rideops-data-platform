"""Consistent JSON error responses.

Every error has the same shape, so the frontend handles errors in one place:
    {"error": {"code": "not_found", "message": "Pipeline 'x' not found", "details": ...}}
"""

import logging

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: object = None) -> None:
        super().__init__(message)
        self.status_code, self.code, self.message, self.details = (
            status_code,
            code,
            message,
            details,
        )


def _body(code: str, message: str, details: object = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details)
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(map(str, e["loc"][1:])), "message": e["msg"]} for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content=_body("validation_error", "Invalid request", details)
        )

    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(PoolTimeout)
    async def database_unavailable(_: Request, exc: Exception) -> JSONResponse:
        logger.error("Database unavailable: %s", exc)
        return JSONResponse(
            status_code=503, content=_body("database_unavailable", "The database is not reachable")
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Log the full traceback server-side; never leak internals to the client.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500, content=_body("internal_error", "Unexpected server error")
        )
