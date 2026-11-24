"""Job types and their limits (§10.5). Handlers register with @job(...)."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

JobHandler = Callable[["JobContext"], None]


@dataclass(frozen=True)
class JobSpec:
    job_type: str
    handler: JobHandler
    timeout_s: int
    max_attempts: int
    priority: int


@dataclass
class JobContext:
    job: dict[str, Any]
    db: Any  # rideops_api.db.Database (metadata DB)

    @property
    def payload(self) -> dict[str, Any]:
        return self.job["payload"]


REGISTRY: dict[str, JobSpec] = {}


def job(job_type: str, *, timeout_s: int, max_attempts: int, priority: int) -> Callable[[JobHandler], JobHandler]:
    """Register a handler. Handlers must bound their own work by `timeout_s` (e.g. with a
    statement_timeout on target connections): Python threads cannot be killed safely."""

    def decorator(func: JobHandler) -> JobHandler:
        if job_type in REGISTRY:
            raise ValueError(f"Job type {job_type!r} registered twice")
        REGISTRY[job_type] = JobSpec(job_type, func, timeout_s, max_attempts, priority)
        return func

    return decorator


def get(job_type: str) -> JobSpec | None:
    return REGISTRY.get(job_type)
