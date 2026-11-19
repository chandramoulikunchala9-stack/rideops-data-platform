"""PostgreSQL pipeline CLI. Every step except `migrate` is recorded as a pipeline run.

Usage:
    python -m rideops.db migrate      # create/upgrade schemas, seed reference data
    python -m rideops.db load         # landing files -> raw (incremental)
    python -m rideops.db transform    # raw -> core -> analytics
    python -m rideops.db quality      # data-quality checks -> ops.data_quality_results
    python -m rideops.db all          # all of the above, in order
"""

import argparse
import logging
import sys

import psycopg

from rideops.config import PostgresSettings, get_settings
from rideops.db.pipelines import migrate, run_pipeline
from rideops.logging_config import configure_logging

logger = logging.getLogger("rideops.db")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rideops.db",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=["migrate", "load", "transform", "quality", "all"])
    args = parser.parse_args(argv)
    configure_logging(get_settings().log_level)
    pg = PostgresSettings()

    steps = {
        "load": ["pg_load_raw"],
        "transform": ["pg_transform_core", "pg_build_analytics"],
        "quality": ["pg_quality_checks"],
        "all": ["pg_load_raw", "pg_transform_core", "pg_build_analytics", "pg_quality_checks"],
    }.get(args.command, [])
    try:
        if args.command in ("migrate", "all"):
            migrate(pg)
        for name in steps:
            run_pipeline(name, pg)
    except psycopg.Error:
        logger.exception("Database step failed (see ops.pipeline_runs for the recorded run)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
