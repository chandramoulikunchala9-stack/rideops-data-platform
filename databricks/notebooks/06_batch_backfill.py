# Databricks notebook source
# MAGIC %md
# MAGIC # 06 · Batch backfill for a date range
# MAGIC
# MAGIC Plain **batch** processing (no streaming): recompute gold daily tables for
# MAGIC `start_date..end_date`, for example after fixing a silver bug. `replaceWhere` makes the
# MAGIC rewrite atomic and limited to the requested dates: other days are untouched, and readers
# MAGIC see either the old or the new data, never a mix.
# MAGIC
# MAGIC **Status:** written for Databricks; cloud verification pending.

# COMMAND ----------

dbutils.widgets.text("catalog", "rideops_dev")
dbutils.widgets.text("start_date", "2026-10-01")
dbutils.widgets.text("end_date", "2026-10-31")
catalog = dbutils.widgets.get("catalog")
start_date, end_date = dbutils.widgets.get("start_date"), dbutils.widgets.get("end_date")

# COMMAND ----------

from pyspark.sql import functions as F

from rideops.spark import gold

predicate = f"booking_date BETWEEN DATE'{start_date}' AND DATE'{end_date}'"
bookings = spark.table(f"{catalog}.silver.bookings").filter(predicate)

for name, build in (("daily_city_summary", gold.daily_city_summary),
                    ("cancellation_summary", gold.cancellation_summary)):
    (build(bookings).write.mode("overwrite")
        .option("replaceWhere", predicate)
        .saveAsTable(f"{catalog}.gold.{name}"))
    print(f"rewrote {name} for {start_date}..{end_date}")

# COMMAND ----------

display(
    spark.table(f"{catalog}.gold.daily_city_summary").filter(predicate)
    .groupBy("booking_date").agg(F.sum("total_bookings").alias("bookings"),
                                 F.sum("gross_revenue").alias("revenue"))
    .orderBy("booking_date")
)
