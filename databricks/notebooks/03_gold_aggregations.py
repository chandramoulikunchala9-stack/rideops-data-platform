# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Gold aggregations (incremental via Change Data Feed)
# MAGIC
# MAGIC Reads the **Change Data Feed** of `silver.bookings` since the version processed last time,
# MAGIC finds the business dates that changed, and rewrites only those dates with `replaceWhere`.
# MAGIC Entity-level tables are small and rebuilt in full. The aggregation functions come from the
# MAGIC tested `rideops.spark.gold` module.
# MAGIC
# MAGIC **Status:** written for Databricks; cloud verification pending.

# COMMAND ----------

dbutils.widgets.text("catalog", "rideops_dev")
catalog = dbutils.widgets.get("catalog")

# COMMAND ----------

from pyspark.sql import functions as F

from rideops.spark import gold

silver = lambda t: spark.table(f"{catalog}.silver.{t}")  # noqa: E731
gold_table = lambda t: f"{catalog}.gold.{t}"  # noqa: E731

# Last processed silver version is kept as a table property on the gold table.
summary_table = gold_table("daily_city_summary")
latest = spark.sql(f"DESCRIBE HISTORY {catalog}.silver.bookings LIMIT 1").first()["version"]
last = None
if spark.catalog.tableExists(summary_table):
    props = {r["key"]: r["value"] for r in spark.sql(f"SHOW TBLPROPERTIES {summary_table}").collect()}
    last = int(props["rideops.last_silver_version"]) if "rideops.last_silver_version" in props else None

if last is None:
    dates = None  # first run: full build
elif last >= latest:
    dates = []
else:
    dates = sorted(
        r["booking_date"]
        for r in spark.read.option("readChangeFeed", "true").option("startingVersion", last + 1)
        .table(f"{catalog}.silver.bookings").select("booking_date").distinct().collect()
        if r["booking_date"]
    )
print("dates to recompute:", "ALL" if dates is None else dates)

# COMMAND ----------

bookings = silver("bookings")
for name, build in (("daily_city_summary", gold.daily_city_summary),
                    ("cancellation_summary", gold.cancellation_summary)):
    if dates == []:
        continue
    scope = bookings if dates is None else bookings.filter(F.col("booking_date").isin(dates))
    writer = build(scope).write.mode("overwrite")
    if dates is not None:
        in_list = ", ".join(f"DATE'{d.isoformat()}'" for d in dates)
        writer = writer.option("replaceWhere", f"booking_date IN ({in_list})")
    writer.saveAsTable(gold_table(name))

spark.sql(f"ALTER TABLE {summary_table} SET TBLPROPERTIES ('rideops.last_silver_version' = '{latest}')")

# COMMAND ----------

full_rebuilds = {
    "driver_performance": gold.driver_performance(bookings, silver("drivers")),
    "customer_activity": gold.customer_activity(bookings, silver("customers")),
    "payment_summary": gold.payment_summary(silver("payments")),
    "vehicle_utilization": gold.vehicle_utilization(bookings, silver("vehicles")),
    "app_funnel_daily": gold.app_funnel_daily(spark.table(f"{catalog}.bronze.app_events")),
}
for name, df in full_rebuilds.items():
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(gold_table(name))

# COMMAND ----------

# MAGIC %md ### Table maintenance
# MAGIC Liquid clustering keeps date + city lookups fast without manual partitioning.
# MAGIC `OPTIMIZE` compacts small files written by frequent incremental updates.

# COMMAND ----------

spark.sql(f"ALTER TABLE {summary_table} CLUSTER BY (booking_date, city)")
spark.sql(f"OPTIMIZE {summary_table}")
display(spark.sql(f"DESCRIBE HISTORY {summary_table} LIMIT 5"))
