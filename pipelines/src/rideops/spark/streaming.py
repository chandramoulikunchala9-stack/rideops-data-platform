"""Structured Streaming for ride events.

Two chained streaming queries, each with its own checkpoint (failure recovery: a restarted
query resumes from the last committed offsets, so nothing is lost or double-counted):

  1. landing/ride_events (JSON files) --> bronze.ride_events      (append, raw strings)
  2. bronze.ride_events (Delta stream) --> gold.ride_event_windows (event-time aggregation)

Query 2 demonstrates event-time processing:
  * event_ts (device time) is used, not arrival time;
  * a watermark of WATERMARK_DELAY lets late events update their window until the watermark
    passes; events later than that are dropped and counted (numRowsDroppedByWatermark);
  * dropDuplicatesWithinWatermark removes at-least-once re-deliveries of the same event_id;
  * with two stateful operators (dedup + aggregation) Spark filters late events against the
    watermark of the *previous* micro-batch and uses the current one for state eviction, so an
    event is dropped only once it is older than the watermark at the end of the prior batch;
  * 15-minute tumbling windows per city and event type, emitted once final (append mode).

`replay_events()` simulates a live feed by writing small JSON files every few seconds, so the
same job can run continuously with a processing-time trigger (`--live`).
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.streaming import StreamingQuery

from rideops.generators.events import RIDE_EVENT_TYPES
from rideops.spark import bronze
from rideops.spark.lakehouse import Lakehouse, parse_ts

logger = logging.getLogger(__name__)

WATERMARK_DELAY = "30 minutes"
WINDOW = "15 minutes"


def windowed_event_counts(events):
    """Pure transformation (testable on a static DataFrame without the streaming parts)."""
    return (
        events.withColumn("event_time", parse_ts("event_ts"))
        .filter(F.col("event_time").isNotNull() & F.col("event_type").isin(*RIDE_EVENT_TYPES))
        .withWatermark("event_time", WATERMARK_DELAY)
        .dropDuplicatesWithinWatermark(["event_id"])
        .groupBy(F.window("event_time", WINDOW), "city", "event_type")
        .agg(F.count("*").alias("events"), F.approx_count_distinct("booking_id").alias("bookings"))
        .select(
            F.col("window.start").alias("window_start"),
            F.col("window.end").alias("window_end"),
            "city", "event_type", "events", "bookings",
        )
    )  # fmt: skip


def _start_window_query(
    spark: SparkSession, lake: Lakehouse, *, live: bool, files_per_trigger: int
) -> StreamingQuery:
    source = (
        spark.readStream.format("delta")
        .option("maxFilesPerTrigger", files_per_trigger)
        .load(lake.bronze("ride_events"))
    )
    writer = (
        windowed_event_counts(source)
        .writeStream.format("delta")
        .outputMode("append")
        .option("checkpointLocation", lake.checkpoint("gold_ride_event_windows"))
        .queryName("gold_ride_event_windows")
    )
    writer = (
        writer.trigger(processingTime="10 seconds") if live else writer.trigger(availableNow=True)
    )
    return writer.start(lake.gold("ride_event_windows"))


def run(
    spark: SparkSession, lake: Lakehouse, *, live: bool = False, files_per_trigger: int = 2
) -> dict[str, int]:
    """Process all available ride events (or run continuously with live=True)."""
    ingested = bronze.ingest(spark, lake, "ride_events", max_files_per_trigger=files_per_trigger)
    query = _start_window_query(spark, lake, live=live, files_per_trigger=files_per_trigger)
    query.awaitTermination()  # with live=True this blocks until stopped (Ctrl+C)
    if query.exception():  # pragma: no cover
        raise query.exception()

    dropped = sum(
        op.get("numRowsDroppedByWatermark", 0)
        for p in query.recentProgress
        for op in p.get("stateOperators", [])
    )
    rows = sum(p["numInputRows"] for p in query.recentProgress)
    logger.info(
        "stream ride_events: ingested=%d processed=%d late_dropped=%d", ingested, rows, dropped
    )
    return {"read": ingested, "written": rows - dropped, "rejected": dropped}


def replay_events(lake: Lakehouse, *, batch_size: int = 500, interval_s: float = 5.0,
                  limit_batches: int | None = None) -> int:  # fmt: skip
    """Copy existing ride events into a new landing partition in small timed batches.

    Simulates a live feed for demos of the continuous stream (and of Auto Loader on Databricks).
    Files are written atomically (temp name + rename) so a reader never sees a partial file.
    """
    sources = sorted(Path(lake.landing("ride_events")).glob("batch_date=*/*.jsonl"))
    today = datetime.now(timezone.utc).date().isoformat()
    target = Path(lake.landing("ride_events")) / f"batch_date={today}"
    target.mkdir(parents=True, exist_ok=True)
    buffer: list[str] = []
    written = 0
    for path in sources:
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            record["event_id"] = f"replay-{written}-{record['event_id']}"
            buffer.append(json.dumps(record))
            if len(buffer) == batch_size:
                tmp = target / f".replay_{written:06d}.tmp"
                tmp.write_text("\n".join(buffer) + "\n", encoding="utf-8")
                tmp.rename(target / f"ride_events_replay_{written:06d}.jsonl")
                written += 1
                buffer.clear()
                logger.info("replayed batch %d (%d events)", written, batch_size)
                if limit_batches and written >= limit_batches:
                    return written
                time.sleep(interval_s)
    return written
