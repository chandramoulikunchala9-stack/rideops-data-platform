"""Executes whitelisted local pipelines for POST /pipelines/{name}/retry.

Safety rules:
  * disabled unless RETRY_RUNNER_ENABLED=true (the API never pretends a retry happened);
  * only pipelines in rideops.db.pipelines.PIPELINES can run (no arbitrary commands);
  * one worker thread and a per-pipeline guard, so the same pipeline never runs twice at once;
  * the run is recorded by RunTracker exactly like a scheduled run (trigger = retry).
"""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from rideops.config import PostgresSettings
from rideops.db.pipelines import PIPELINES, run_pipeline

logger = logging.getLogger(__name__)


class AlreadyRunningError(RuntimeError):
    pass


class JobRunner:
    def __init__(self, enabled: bool, settings: PostgresSettings | None = None) -> None:
        self.enabled = enabled
        self._settings = settings  # None = read POSTGRES_* from the environment at run time
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job-runner")
        self._active: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def can_run(pipeline_name: str) -> bool:
        return pipeline_name in PIPELINES

    def is_active(self, pipeline_name: str) -> bool:
        with self._lock:
            return pipeline_name in self._active

    def submit(self, pipeline_name: str, retry_of_run_id: str | None) -> Future:
        with self._lock:
            if pipeline_name in self._active:
                raise AlreadyRunningError(pipeline_name)
            self._active.add(pipeline_name)
        return self._executor.submit(self._run, pipeline_name, retry_of_run_id)

    def _run(self, pipeline_name: str, retry_of_run_id: str | None) -> str | None:
        try:
            trigger = "retry" if retry_of_run_id else "api"
            return run_pipeline(pipeline_name, self._settings or PostgresSettings(), trigger=trigger,
                                retry_of=retry_of_run_id)  # fmt: skip
        except Exception:  # the failure is recorded in ops.pipeline_runs by RunTracker
            logger.exception("Job %s failed", pipeline_name)
            return None
        finally:
            with self._lock:
                self._active.discard(pipeline_name)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
