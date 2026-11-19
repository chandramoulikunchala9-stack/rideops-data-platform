"""Named PostgreSQL pipelines, shared by the CLI (`python -m rideops.db`) and the API job runner."""

import logging

from rideops.config import PostgresSettings, get_settings
from rideops.db.connection import connect
from rideops.db.migrate import apply_migrations, seed_reference_data
from rideops.db.transform import build_analytics, transform_core
from rideops.ingestion.postgres_loader import load_landing
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
