"""Record real pipeline executions in ops.pipeline_runs and ops.pipeline_run_logs.

Usage:
    with RunTracker("pg_load_raw", layer="bronze") as run:
        run.log("INFO", "starting")
        ...
        run.add_counts(read=100, written=98, rejected=2)

The tracker uses its own autocommit connection. If the pipeline's work fails and its
transaction is rolled back, the RUNNING -> FAILED record and the error message still persist,
which is exactly what an on-call engineer needs to see.
"""

import logging
import secrets
import time
from datetime import datetime, timezone
from types import TracebackType

import psycopg

from rideops.config import PostgresSettings
from rideops.db.connection import connect

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 4000


class RunTracker:
    def __init__(
        self,
        pipeline_name: str,
        *,
        layer: str,
        trigger: str = "manual",
        retry_of_run_id: str | None = None,
        settings: PostgresSettings | None = None,
    ) -> None:
        self.pipeline_name = pipeline_name
        self.layer = layer
        self.trigger = trigger
        self.retry_of_run_id = retry_of_run_id
        self._settings = settings
        self._conn: psycopg.Connection | None = None
        self._started = 0.0
        self.records_read = 0
        self.records_written = 0
        self.records_rejected = 0
        now = datetime.now(timezone.utc)
        self.run_id = f"RUN-{now:%Y%m%d%H%M%S}-{pipeline_name}-{secrets.token_hex(3)}"

    # ------------------------------------------------------------------ context manager
    def __enter__(self) -> "RunTracker":
        self._conn = connect(self._settings, autocommit=True)
        self._started = time.perf_counter()
        retry_count = 0
        if self.retry_of_run_id:
            row = self._conn.execute(
                "SELECT retry_count FROM ops.pipeline_runs WHERE run_id = %s",
                (self.retry_of_run_id,),
            ).fetchone()
            retry_count = (row[0] if row else 0) + 1
        self._conn.execute(
            """
            INSERT INTO ops.pipeline_runs (
                run_id, pipeline_name, layer, trigger, status, start_ts, retry_count,
                run_source, retry_of_run_id
            ) VALUES (%s, %s, %s, %s, 'RUNNING', now(), %s, 'local_run', %s)
            """,
            (self.run_id, self.pipeline_name, self.layer, self.trigger, retry_count,
             self.retry_of_run_id),
        )  # fmt: skip
        self.log("INFO", f"Run started (trigger={self.trigger})")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._conn is not None
        status = "SUCCESS" if exc is None else "FAILED"
        error = None if exc is None else f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
        duration = round(time.perf_counter() - self._started)
        try:
            if error:
                self.log("ERROR", error)
            self.log("INFO", f"Run finished with status {status} in {duration}s")
            self._conn.execute(
                """
                UPDATE ops.pipeline_runs SET
                    status = %s, end_ts = now(), duration_sec = %s, records_read = %s,
                    records_written = %s, records_rejected = %s, error_message = %s
                WHERE run_id = %s
                """,
                (status, duration, self.records_read, self.records_written,
                 self.records_rejected, error, self.run_id),
            )  # fmt: skip
        finally:
            self._conn.close()
        # Returning None re-raises any exception: the tracker records failures, never hides them.

    # ------------------------------------------------------------------ helpers
    def log(self, level: str, message: str) -> None:
        getattr(logger, level.lower(), logger.info)("[%s] %s", self.pipeline_name, message)
        if self._conn is not None and not self._conn.closed:
            self._conn.execute(
                "INSERT INTO ops.pipeline_run_logs (run_id, level, message) VALUES (%s, %s, %s)",
                (self.run_id, level, message[:MAX_ERROR_LENGTH]),
            )

    def add_counts(self, *, read: int = 0, written: int = 0, rejected: int = 0) -> None:
        self.records_read += read
        self.records_written += written
        self.records_rejected += rejected
