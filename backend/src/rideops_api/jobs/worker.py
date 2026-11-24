"""Worker loop: claim → run handler (with heartbeats) → complete / fail."""

import logging
import os
import socket
import threading
import uuid

from rideops_api.db import Database
from rideops_api.jobs import queue, registry

logger = logging.getLogger(__name__)
HEARTBEAT_S = 15.0


def new_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class Worker:
    def __init__(self, db: Database, *, worker_id: str | None = None, job_types: list[str] | None = None,
                 heartbeat_s: float = HEARTBEAT_S, settings=None) -> None:  # fmt: skip
        self.db = db
        self.settings = settings
        self.worker_id = worker_id or new_worker_id()
        self.job_types = job_types
        self.heartbeat_s = heartbeat_s

    def run_once(self) -> dict | None:
        """Claim and execute one job. Returns the claimed job row, or None if the queue is idle."""
        with self.db.pool.connection() as conn:
            job = queue.claim(conn, self.worker_id, self.job_types)
        if job is None:
            return None
        spec = registry.get(job["job_type"])
        stop = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(job["id"], stop), daemon=True)
        beat.start()
        try:
            if spec is None:
                raise LookupError(f"No handler registered for job type {job['job_type']!r}")
            spec.handler(registry.JobContext(job=job, db=self.db, settings=self.settings))
        except Exception as exc:  # noqa: BLE001 - any handler failure is recorded on the job
            stop.set()
            with self.db.pool.connection() as conn:
                status = queue.fail(conn, job["id"], self.worker_id, f"{type(exc).__name__}: {exc}")
            logger.warning("Job %s (%s) failed attempt %d: %s -> %s", job["id"], job["job_type"],
                           job["attempts"], exc, status)  # fmt: skip
        else:
            stop.set()
            with self.db.pool.connection() as conn:
                queue.complete(conn, job["id"], self.worker_id)
        finally:
            stop.set()
            beat.join(timeout=1)
        return job

    def drain(self, max_jobs: int = 100) -> int:
        n = 0
        while n < max_jobs and self.run_once() is not None:
            n += 1
        return n

    def run_forever(self, stop: threading.Event, poll_interval_s: float = 1.0) -> None:
        logger.info("Worker %s started", self.worker_id)
        while not stop.is_set():
            try:
                if self.run_once() is None:
                    stop.wait(poll_interval_s)
            except Exception:  # noqa: BLE001 - e.g. DB briefly down; keep the loop alive
                logger.exception("Worker loop error")
                stop.wait(max(poll_interval_s, 5.0))
        logger.info("Worker %s stopped", self.worker_id)

    def _heartbeat(self, job_id: int, stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_s):
            try:
                with self.db.pool.connection() as conn:
                    queue.heartbeat(conn, job_id, self.worker_id)
            except Exception:  # noqa: BLE001
                logger.warning("Heartbeat failed for job %s", job_id)
