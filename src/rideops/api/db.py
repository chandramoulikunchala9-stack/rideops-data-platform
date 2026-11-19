"""Database access for the API: a connection pool plus tiny query helpers.

A pool keeps a few connections open and lends them to requests, instead of paying the
connect/authenticate cost (tens of milliseconds) on every request.
"""

from typing import Any

from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from rideops.config import PostgresSettings


class Database:
    def __init__(
        self, settings: PostgresSettings | None = None, *, max_size: int = 8, timeout: float = 5.0
    ) -> None:
        settings = settings or PostgresSettings()
        self.pool = ConnectionPool(
            settings.conninfo(),
            min_size=1,
            max_size=max_size,
            open=False,
            timeout=timeout,  # wait at most this long for a connection, then fail fast
            kwargs={"row_factory": dict_row, "autocommit": True},
        )

    def open(self) -> None:
        self.pool.open(wait=False)  # start even if the DB is down; /health reports it

    def close(self) -> None:
        self.pool.close()

    def fetch_all(self, sql: str, params: dict | tuple | None = None) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def fetch_one(self, sql: str, params: dict | tuple | None = None) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: dict | tuple | None = None) -> Any:
        row = self.fetch_one(sql, params)
        return next(iter(row.values())) if row else None


def get_db(request: Request) -> Database:
    """FastAPI dependency: the Database created at startup (overridden in tests)."""
    return request.app.state.db
