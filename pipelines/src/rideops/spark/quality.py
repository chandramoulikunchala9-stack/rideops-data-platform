"""Data-quality expectations on the lakehouse (silver + quarantine).

Each expectation counts failing rows over a total. Results use the same PASS/WARN/FAIL rule as
the SQL checks and, when PostgreSQL is configured, are stored in ops.data_quality_results with
a `lakehouse_` prefix so the dashboard shows both pipelines side by side.
"""

import logging
from dataclasses import dataclass

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from rideops.quality.checks import CheckResult, decide_status
from rideops.spark.lakehouse import Lakehouse, table_exists

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Expectation:
    name: str
    entity: str
    check_type: str
    failing: str  # Spark SQL condition that marks a row as failing (same syntax as DLT rules)
    threshold: float


# Plain SQL strings, not Column objects: they can be defined at import time without a running
# SparkSession, and the same text works in DLT expectations and Databricks SQL.
EXPECTATIONS: tuple[Expectation, ...] = (
    Expectation("bookings_fare_non_negative", "bookings", "validity", "fare_amount < 0", 0.0),
    Expectation("bookings_dropoff_after_pickup", "bookings", "validity",
                "dropoff_at < pickup_at", 0.0),
    Expectation("bookings_completed_have_driver", "bookings", "validity",
                "status = 'completed' AND driver_id IS NULL", 0.0),
    Expectation("bookings_missing_payment_method", "bookings", "completeness",
                "payment_method IS NULL", 0.02),
    Expectation("customers_missing_email", "customers", "completeness", "email IS NULL", 0.05),
    Expectation("drivers_rating_in_range", "drivers", "validity", "NOT rating BETWEEN 1 AND 5", 0.0),
    Expectation("payments_currency_inr", "payments", "validity", "currency <> 'INR'", 0.0),
)  # fmt: skip


def evaluate(df: DataFrame, expectation: Expectation) -> tuple[int, int]:
    row = df.agg(
        F.sum(F.when(F.expr(expectation.failing), 1).otherwise(0)).alias("failed"),
        F.count("*").alias("total"),
    ).collect()[0]
    return int(row["failed"] or 0), int(row["total"])


def run_expectations(spark: SparkSession, lake: Lakehouse) -> list[CheckResult]:
    results: list[CheckResult] = []
    tables: dict[str, DataFrame] = {}
    for exp in EXPECTATIONS:
        path = lake.silver(exp.entity)
        if not table_exists(spark, path):
            continue
        df = tables.setdefault(exp.entity, spark.read.format("delta").load(path))
        failed, total = evaluate(df, exp)
        results.append(CheckResult(
            f"lakehouse_{exp.name}", exp.entity, exp.check_type, failed, total, exp.threshold, {},
            decide_status(failed, total, exp.threshold),
        ))  # fmt: skip

    # Uniqueness of every silver primary key, and the quarantine rate per entity.
    keys = {"bookings": "booking_id", "customers": "customer_id", "drivers": "driver_id",
            "vehicles": "vehicle_id", "payments": "payment_id"}  # fmt: skip
    quarantine = (
        spark.read.format("delta").load(lake.quarantine)
        if table_exists(spark, lake.quarantine)
        else None
    )
    for entity, key in keys.items():
        path = lake.silver(entity)
        if not table_exists(spark, path):
            continue
        df = tables.setdefault(entity, spark.read.format("delta").load(path))
        total = df.count()
        dupes = total - df.select(key).distinct().count()
        results.append(CheckResult(f"lakehouse_{entity}_key_unique", entity, "uniqueness", dupes,
                                   total, 0.0, {}, decide_status(dupes, total, 0.0)))  # fmt: skip
        if quarantine is not None:
            rejected = quarantine.filter(F.col("entity") == entity).count()
            results.append(CheckResult(
                f"lakehouse_{entity}_quarantine_rate", entity, "validity", rejected,
                total + rejected, 0.02, {}, decide_status(rejected, total + rejected, 0.02),
            ))  # fmt: skip
    for r in results:
        (logger.warning if r.status != "PASS" else logger.info)(
            "DQ %-4s %-40s %d/%d", r.status, r.check_name, r.failed_rows, r.total_rows
        )
    return results


def store_results(results: list[CheckResult], run_id: str | None) -> bool:
    """Write results to PostgreSQL if it is configured; returns False when skipped."""
    try:
        from psycopg.types.json import Jsonb

        from rideops.db.connection import connect

        with connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO ops.data_quality_results (run_id, check_name, entity, check_type,
                    status, failed_rows, total_rows, threshold, details)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [(run_id, r.check_name, r.entity, r.check_type, r.status, r.failed_rows,
                  r.total_rows, r.threshold, Jsonb(r.details)) for r in results],
            )  # fmt: skip
        return True
    except Exception as exc:  # noqa: BLE001 - Postgres is optional for lakehouse runs
        logger.warning("Data-quality results not stored in PostgreSQL: %s", exc)
        return False
