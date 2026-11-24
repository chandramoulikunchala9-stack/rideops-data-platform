"""In-process worker + scheduler threads for LENS_ROLE=all (single free-tier process, ADR-6)."""

import threading

from rideops_api.db import Database
from rideops_api.jobs import scheduler
from rideops_api.jobs.worker import Worker
from rideops_api.settings import LensSettings


class Background:
    def __init__(
        self, db: Database, settings: LensSettings, *, worker: bool, scheduler: bool
    ) -> None:
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []
        if worker:
            args = (self.stop, settings.worker_poll_interval_s)
            self.threads.append(
                threading.Thread(
                    target=Worker(db).run_forever, args=args, name="lens-worker", daemon=True
                )
            )
        if scheduler:
            args = (db, self.stop, settings.scheduler_interval_s)
            self.threads.append(
                threading.Thread(
                    target=_scheduler_loop, args=args, name="lens-scheduler", daemon=True
                )
            )

    def start(self) -> None:
        for t in self.threads:
            t.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        self.stop.set()
        for t in self.threads:
            t.join(timeout)


def _scheduler_loop(db: Database, stop: threading.Event, interval_s: float) -> None:
    scheduler.run_forever(db, stop, interval_s)
