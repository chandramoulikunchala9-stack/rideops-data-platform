"""Bronze: landing files -> append-only Delta tables, incrementally.

Uses a Structured Streaming file source with `trigger(availableNow=True)`:
  * the checkpoint remembers which files were already ingested, so each run picks up only
    new files and every file is ingested exactly once (the open-source equivalent of
    Databricks Auto Loader, see databricks/notebooks/01_bronze_autoloader.py);
  * availableNow processes everything currently available and then stops, so the same code
    works as a scheduled batch job or as a continuously running stream.

Bronze keeps every column as a string, exactly as delivered, plus ingestion metadata. Malformed
JSON lines are kept in `_corrupt_record` instead of failing the job or being dropped.
"""

import logging

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

from rideops.generators.events import APP_EVENT_FIELDS, RIDE_EVENT_FIELDS
from rideops.ingestion.postgres_loader import CONTRACTS
from rideops.spark.lakehouse import Lakehouse

logger = logging.getLogger(__name__)

# entity -> (file format, field names). Reuses the source contracts defined for Postgres.
SOURCES: dict[str, tuple[str, tuple[str, ...]]] = {
    **CONTRACTS,
    "ride_events": ("jsonl", RIDE_EVENT_FIELDS),
    "app_events": ("jsonl", APP_EVENT_FIELDS),
}


def string_schema(fields: tuple[str, ...], with_corrupt: bool = False) -> StructType:
    columns = [StructField(name, StringType()) for name in fields]
    if with_corrupt:
        columns.append(StructField("_corrupt_record", StringType()))
    return StructType(columns)


def read_landing_stream(
    spark: SparkSession, lake: Lakehouse, entity: str, max_files_per_trigger: int | None = None
) -> DataFrame:
    fmt, fields = SOURCES[entity]
    reader = spark.readStream.schema(string_schema(fields, with_corrupt=fmt == "jsonl"))
    if max_files_per_trigger:
        reader = reader.option("maxFilesPerTrigger", max_files_per_trigger)
    if fmt == "csv":
        df = reader.option("header", "true").csv(f"{lake.landing(entity)}/batch_date=*/*.csv")
    else:
        df = (
            reader.option("mode", "PERMISSIVE")
            .option("columnNameOfCorruptRecord", "_corrupt_record")
            .json(f"{lake.landing(entity)}/batch_date=*/*.jsonl")
        )
    source_file = F.col("_metadata.file_path")
    return (
        df.withColumn("_source_file", source_file)
        .withColumn("_batch_date", F.regexp_extract(source_file, r"batch_date=(\d{4}-\d{2}-\d{2})", 1))
        .withColumn("_batch_id", F.regexp_extract(source_file, r"_(\d+)\.\w+$", 1))
        .withColumn("_ingested_at", F.current_timestamp())
    )  # fmt: skip


def ingest(
    spark: SparkSession, lake: Lakehouse, entity: str, max_files_per_trigger: int | None = None
) -> int:
    """Ingest all new landing files of one entity into bronze. Returns rows ingested."""
    query = (
        read_landing_stream(spark, lake, entity, max_files_per_trigger)
        .writeStream.format("delta")
        .outputMode("append")
        .option("checkpointLocation", lake.checkpoint(f"bronze_{entity}"))
        .trigger(availableNow=True)
        .queryName(f"bronze_{entity}")
        .start(lake.bronze(entity))
    )
    query.awaitTermination()
    if query.exception():  # pragma: no cover - surfaced to the caller / run tracker
        raise query.exception()
    rows = sum(p["numInputRows"] for p in query.recentProgress)
    logger.info("bronze %-13s +%d rows", entity, rows)
    return rows
