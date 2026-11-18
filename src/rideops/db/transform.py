"""Run the SQL transformations in sql/transform/ (raw -> core -> analytics)."""

import logging
from pathlib import Path

import psycopg

from rideops.config import SQL_DIR

logger = logging.getLogger(__name__)

CORE_FILES = ("10_core_dimensions.sql", "20_core_facts.sql", "25_ops_sources.sql")
ANALYTICS_FILES = ("30_analytics.sql",)

_COUNTS_SQL = """
SELECT
    (SELECT count(*) FROM core.vehicles) + (SELECT count(*) FROM core.drivers)
      + (SELECT count(*) FROM core.customers) + (SELECT count(*) FROM core.bookings)
      + (SELECT count(*) FROM core.payments) + (SELECT count(*) FROM ops.cloud_costs)
      + (SELECT count(*) FROM ops.pipeline_runs WHERE run_source = 'simulated_history'),
    (SELECT count(*) FROM core.rejected_records)
"""

_PENDING_SQL = """
SELECT
    (SELECT count(*) FROM raw.vehicles WHERE _loaded_at > ops.watermark('vehicles'))
  + (SELECT count(*) FROM raw.drivers WHERE _loaded_at > ops.watermark('drivers'))
  + (SELECT count(*) FROM raw.customers WHERE _loaded_at > ops.watermark('customers'))
  + (SELECT count(*) FROM raw.bookings WHERE _loaded_at > ops.watermark('bookings'))
  + (SELECT count(*) FROM raw.payments WHERE _loaded_at > ops.watermark('payments'))
  + (SELECT count(*) FROM raw.pipeline_runs WHERE _loaded_at > ops.watermark('pipeline_runs'))
  + (SELECT count(*) FROM raw.cloud_costs WHERE _loaded_at > ops.watermark('cloud_costs'))
"""


def run_sql_file(conn: psycopg.Connection, path: Path) -> None:
    """Execute one SQL file in a single transaction (all statements succeed or none do)."""
    with conn.transaction():
        conn.execute(path.read_text(encoding="utf-8"))
    logger.info("Executed %s", path.name)


def transform_core(conn: psycopg.Connection, sql_dir: Path = SQL_DIR) -> dict[str, int]:
    """Incremental raw -> core. Returns rows read, written (net new) and rejected."""
    pending = conn.execute(_PENDING_SQL).fetchone()[0]
    before_rows, before_rejected = conn.execute(_COUNTS_SQL).fetchone()
    for name in CORE_FILES:
        run_sql_file(conn, sql_dir / "transform" / name)
    after_rows, after_rejected = conn.execute(_COUNTS_SQL).fetchone()
    return {
        "read": pending,
        "written": after_rows - before_rows,
        "rejected": after_rejected - before_rejected,
    }


def build_analytics(conn: psycopg.Connection, sql_dir: Path = SQL_DIR) -> dict[str, int]:
    for name in ANALYTICS_FILES:
        run_sql_file(conn, sql_dir / "transform" / name)
    rows = conn.execute("SELECT coalesce(sum(row_count), 0) FROM analytics.refresh_log").fetchone()
    return {"written": int(rows[0])}
