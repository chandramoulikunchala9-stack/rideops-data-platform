# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Ride events: Auto Loader + Structured Streaming
# MAGIC
# MAGIC * **Auto Loader** picks up new JSON event files from the landing volume.
# MAGIC * **Event time + watermark**: windows are based on `event_ts` (device time). Events up to
# MAGIC   30 minutes late still update their window; later events are dropped and counted.
# MAGIC * **Deduplication** with `dropDuplicatesWithinWatermark` absorbs at-least-once re-deliveries.
# MAGIC * **Checkpoint** = failure recovery: a restarted run resumes exactly where it stopped.
# MAGIC
# MAGIC Local equivalent (tested): `src/rideops/spark/streaming.py`.
# MAGIC **Status:** written for Databricks; cloud verification pending.

# COMMAND ----------

dbutils.widgets.text("catalog", "rideops_dev")
dbutils.widgets.dropdown("mode", "availableNow", ["availableNow", "continuous"])
catalog = dbutils.widgets.get("catalog")
mode = dbutils.widgets.get("mode")
LANDING = f"/Volumes/{catalog}/landing/raw/ride_events/"
CHECKPOINTS = f"/Volumes/{catalog}/landing/checkpoints"

# COMMAND ----------

from pyspark.sql import functions as F

from rideops.spark.streaming import windowed_event_counts

raw = (
    spark.readStream.format("cloudFiles")
    .option("cloudFiles.format", "json")
    .option("cloudFiles.schemaLocation", f"{CHECKPOINTS}/schemas/ride_events")
    .option("cloudFiles.inferColumnTypes", "false")
    .load(LANDING)
    .withColumn("_source_file", F.col("_metadata.file_path"))
    .withColumn("_ingested_at", F.current_timestamp())
)

trigger = {"availableNow": True} if mode == "availableNow" else {"processingTime": "30 seconds"}

bronze_query = (
    raw.writeStream.option("checkpointLocation", f"{CHECKPOINTS}/bronze_ride_events")
    .trigger(**trigger)
    .toTable(f"{catalog}.bronze.ride_events")
)

# COMMAND ----------

window_query = (
    windowed_event_counts(spark.readStream.table(f"{catalog}.bronze.ride_events"))
    .writeStream.outputMode("append")
    .option("checkpointLocation", f"{CHECKPOINTS}/gold_ride_event_windows")
    .trigger(**trigger)
    .toTable(f"{catalog}.gold.ride_event_windows")
)

# COMMAND ----------

if mode == "availableNow":
    bronze_query.awaitTermination()
    window_query.awaitTermination()
    dropped = sum(op.get("numRowsDroppedByWatermark", 0)
                  for p in window_query.recentProgress for op in p.get("stateOperators", []))
    print(f"late events dropped by the watermark: {dropped}")

# COMMAND ----------

# MAGIC %md ### Latest windows (most recent first)

# COMMAND ----------

display(spark.table(f"{catalog}.gold.ride_event_windows").orderBy(F.desc("window_start")).limit(50))
