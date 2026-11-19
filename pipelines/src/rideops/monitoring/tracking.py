"""Run tracking that degrades gracefully when PostgreSQL is not available."""

import logging
from contextlib import AbstractContextManager

logger = logging.getLogger(__name__)


class NullTracker(AbstractContextManager):
    """Same interface as RunTracker, but only logs. Used when PostgreSQL is not configured."""

    def __init__(self, pipeline_name: str) -> None:
        self.pipeline_name = pipeline_name
        self.run_id = None

    def __exit__(self, *exc) -> None:
        return None

    def log(self, level: str, message: str) -> None:
        getattr(logger, level.lower(), logger.info)("[%s] %s", self.pipeline_name, message)

    def add_counts(self, **_counts: int) -> None:
        return None


def open_tracker(pipeline_name: str, *, layer: str, trigger: str = "manual",
                 retry_of_run_id: str | None = None):  # fmt: skip
    """A RunTracker if PostgreSQL is reachable, otherwise a NullTracker (with a warning)."""
    try:
        import psycopg
        from pydantic import ValidationError

        from rideops.monitoring.tracker import RunTracker

        tracker = RunTracker(pipeline_name, layer=layer, trigger=trigger,
                             retry_of_run_id=retry_of_run_id)  # fmt: skip
        tracker.__enter__()  # connects and inserts the RUNNING row; raises if unreachable
        return _Entered(tracker)
    except (ImportError, ValidationError, psycopg.OperationalError) as exc:
        logger.warning("Run tracking disabled for %s (PostgreSQL unavailable: %s)",
                       pipeline_name, str(exc).splitlines()[0])  # fmt: skip
        return NullTracker(pipeline_name)


class _Entered(AbstractContextManager):
    """Wraps an already-entered RunTracker so `with open_tracker(...) as run:` works."""

    def __init__(self, tracker) -> None:
        self._tracker = tracker
        self.run_id = tracker.run_id

    def __enter__(self):
        return self._tracker

    def __exit__(self, exc_type, exc, tb):
        return self._tracker.__exit__(exc_type, exc, tb)
