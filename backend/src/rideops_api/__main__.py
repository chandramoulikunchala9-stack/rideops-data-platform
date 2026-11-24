"""`python -m rideops_api <command>`

migrate                 apply Lens metadata migrations (schema `app`) to head
serve [--role ROLE]     run the API; ROLE all|api (default: LENS_ROLE)
worker                  run a job worker only
scheduler               run the scheduler only
"""

import argparse
import logging
import os
import signal
import threading

from rideops.config import get_settings
from rideops.logging_config import configure_logging


def _run_loop(target) -> None:
    from rideops_api.db import Database
    from rideops_api.jobs import tasks  # noqa: F401 - registers handlers
    from rideops_api.settings import get_lens_settings

    settings = get_lens_settings()
    db = Database(conninfo=settings.metadata_conninfo())
    db.open()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        target(db, stop, settings)
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rideops_api")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    serve = sub.add_parser("serve")
    serve.add_argument("--role", choices=["all", "api"])
    sub.add_parser("worker")
    sub.add_parser("scheduler")
    sub.add_parser(
        "rotate-keys", help="re-wrap every stored secret with the active LENS_MASTER_KEYS version"
    )
    sub.add_parser("rollup", help="recompute hourly/daily metric roll-ups now")
    args = parser.parse_args(argv)
    configure_logging(get_settings().log_level)
    log = logging.getLogger("rideops_api")

    if args.command == "migrate":
        from rideops_api import migrate

        migrate.upgrade()
        log.info("Lens migrations at %s", migrate.head_revision())
    elif args.command == "serve":
        if args.role:
            os.environ["LENS_ROLE"] = args.role
        from rideops_api.main import run

        run()
    elif args.command == "worker":
        from rideops_api.jobs.worker import Worker

        _run_loop(
            lambda db, stop, s: Worker(db, settings=s).run_forever(stop, s.worker_poll_interval_s)
        )
    elif args.command == "scheduler":
        from rideops_api.jobs import scheduler

        _run_loop(lambda db, stop, s: scheduler.run_forever(db, stop, s.scheduler_interval_s))
    elif args.command in ("rotate-keys", "rollup"):
        from rideops_api.db import Database
        from rideops_api.settings import get_lens_settings

        settings = get_lens_settings()
        db = Database(conninfo=settings.metadata_conninfo())
        db.open()
        try:
            if args.command == "rollup":
                from rideops_api.lens.status import rollups

                log.info("Roll-ups: %s", rollups.rollup(db, settings))
            else:
                from rideops_api.lens.security import rotation

                total: dict[str, int] = {}
                while True:
                    done = rotation.rewrap_all(db, settings)
                    total = {k: total.get(k, 0) + v for k, v in done.items()}
                    if not any(done.values()):
                        break
                log.info(
                    "Re-wrapped %s; %d secrets still on old key versions",
                    total,
                    rotation.remaining(db, settings),
                )
        finally:
            db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
