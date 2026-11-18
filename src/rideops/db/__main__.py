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
from rideops.db.connection import connect
from rideops.db.migrate import apply_migrations, seed_reference_data
from rideops.db.transform import build_analytics, transform_core
from rideops.ingestion.postgres_loader import load_landing
from rideops.logging_config import configure_logging
from rideops.monitoring.tracker import RunTracker
from rideops.quality.checks import run_checks

logger = logging.getLogger("rideops.db")

# Pipeline names that can also be started (or retried) by the API's job runner.
PIPELINES = {
    "pg_load_raw": "bronze",
    "pg_transform_core": "silver",
    "pg_build_analytics": "gold",
    "pg_quality_checks": "silver",
}


def migrate(pg: PostgresSettings) -> None:
    with connect(pg) as conn:
        applied = apply_migrations(conn)
        seed_reference_data(conn)
    logger.info("Migrations applied: %s", applied or "none (up to date)")


def run_pipeline(
    name: str, pg: PostgresSettings, *, trigger: str = "manual", retry_of: str | None = None
) -> str:
    """Run one named pipeline inside a RunTracker. Returns the run_id."""
    settings = get_settings()
    with RunTracker(name, layer=PIPELINES[name], trigger=trigger, retry_of_run_id=retry_of,
                    settings=pg) as run, connect(pg) as conn:  # fmt: skip
        if name == "pg_load_raw":
            summary = load_landing(conn, settings.data_dir)
            run.add_counts(read=summary.rows, written=summary.rows)
            run.log("INFO", f"files loaded={summary.loaded} skipped={summary.skipped} "
                            f"failed={summary.failed}")  # fmt: skip
            for failure in summary.failures:
                run.log("WARNING", failure)
        elif name == "pg_transform_core":
            counts = transform_core(conn)
            run.add_counts(**counts)
            run.log("INFO", f"core transform: {counts}")
        elif name == "pg_build_analytics":
            counts = build_analytics(conn)
            run.add_counts(**counts)
            run.log("INFO", f"analytics rows: {counts['written']}")
        elif name == "pg_quality_checks":
            results = run_checks(conn, run_id=run.run_id)
            failed = [r.check_name for r in results if r.status == "FAIL"]
            run.add_counts(read=len(results), written=len(results), rejected=len(failed))
            run.log("WARNING" if failed else "INFO", f"checks failed: {failed or 'none'}")
        return run.run_id


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
