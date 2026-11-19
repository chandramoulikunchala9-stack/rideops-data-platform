"""Export gold Delta tables to PostgreSQL schema `lakehouse` (the serving layer).

Gold tables are small aggregates, so rows are collected to the driver and written with COPY.
(For large tables you would use Spark's JDBC writer with batching and parallel partitions.)
Each table is replaced inside one transaction: the API never sees a half-exported table.
"""

import logging

from pyspark.sql import SparkSession
from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    DataType,
    DateType,
    DecimalType,
    DoubleType,
    FloatType,
    IntegerType,
    LongType,
    StructType,
    TimestampType,
)

from rideops.db.connection import connect
from rideops.spark.lakehouse import Lakehouse, table_exists

logger = logging.getLogger(__name__)

GOLD_TABLES = (
    "daily_city_summary", "cancellation_summary", "driver_performance", "customer_activity",
    "payment_summary", "vehicle_utilization", "app_funnel_daily", "ride_event_windows",
)  # fmt: skip


def pg_type(dtype: DataType) -> str:
    if isinstance(dtype, DecimalType):
        return f"numeric({dtype.precision},{dtype.scale})"
    if isinstance(dtype, ArrayType):
        return f"{pg_type(dtype.elementType)}[]"
    simple = {
        IntegerType: "integer", LongType: "bigint", DoubleType: "double precision",
        FloatType: "real", BooleanType: "boolean", DateType: "date",
        TimestampType: "timestamptz",
    }  # fmt: skip
    return next((v for k, v in simple.items() if isinstance(dtype, k)), "text")


def create_table_sql(table: str, schema: StructType) -> str:
    columns = ",\n    ".join(f'"{f.name}" {pg_type(f.dataType)}' for f in schema.fields)
    return f"CREATE TABLE lakehouse.{table} (\n    {columns}\n)"


def export_gold(spark: SparkSession, lake: Lakehouse) -> dict[str, int]:
    written = 0
    with connect() as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS lakehouse")
        conn.commit()
        for table in GOLD_TABLES:
            path = lake.gold(table)
            if not table_exists(spark, path):
                continue
            df = spark.read.format("delta").load(path)
            rows = df.collect()
            columns = ", ".join(f'"{c}"' for c in df.columns)
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS lakehouse.{table}")
                cur.execute(create_table_sql(table, df.schema))
                with cur.copy(f"COPY lakehouse.{table} ({columns}) FROM STDIN") as copy:
                    for row in rows:
                        copy.write_row(tuple(row))
            logger.info("exported lakehouse.%-22s rows=%d", table, len(rows))
            written += len(rows)
    return {"written": written}
