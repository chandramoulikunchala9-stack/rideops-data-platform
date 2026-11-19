# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Silver: validate, quarantine and MERGE (incremental)
# MAGIC
# MAGIC Reads **only new bronze rows** (Delta streaming source + checkpoint) and, per micro-batch:
# MAGIC 1. applies the business rules from the `rideops` wheel (`rideops.spark.silver`): the *same,
# MAGIC    unit-tested* functions used locally;
# MAGIC 2. appends untrusted rows to `silver._quarantine` with the reason;
# MAGIC 3. `MERGE`s the latest version of each key into the silver table.
# MAGIC
# MAGIC **Status:** written for Databricks; cloud verification pending.

# COMMAND ----------

dbutils.widgets.text("catalog", "rideops_dev")
catalog = dbutils.widgets.get("catalog")
CHECKPOINTS = f"/Volumes/{catalog}/landing/checkpoints"

# COMMAND ----------

from delta.tables import DeltaTable
from pyspark.sql import functions as F

from rideops.generators.reference import CITIES, VEHICLE_TYPES
from rideops.spark.silver import SPECS, References, split_valid_rejected


def table(name: str) -> str:
    return f"{catalog}.silver.{name}"


def references() -> References:
    def current(entity, key):
        return spark.table(table(entity)).select(key) if spark.catalog.tableExists(table(entity)) else None

    return References(
        cities=spark.createDataFrame([(c.code,) for c in CITIES], "city_code string"),
        vehicle_types=spark.createDataFrame([(t.name,) for t in VEHICLE_TYPES], "vehicle_type string"),
        vehicles=current("vehicles", "vehicle_id"),
        drivers=current("drivers", "driver_id"),
        customers=current("customers", "customer_id"),
        bookings=current("bookings", "booking_id"),
    )


def process(spec) -> dict:
    counts = {"written": 0, "rejected": 0}

    def handle(batch, batch_id):
        staged = spec.transform(batch, references()).persist()
        valid, rejected = split_valid_rejected(staged, spec)
        rejected.write.mode("append").saveAsTable(table("_quarantine"))
        if not spark.catalog.tableExists(table(spec.name)):
            valid.limit(0).write.saveAsTable(table(spec.name))
        merge = DeltaTable.forName(spark, table(spec.name)).alias("t").merge(
            valid.alias("s"), f"t.{spec.key} = s.{spec.key}"
        )
        if spec.upsert:
            merge = merge.whenMatchedUpdateAll(condition="s.updated_at > t.updated_at")
        merge.whenNotMatchedInsertAll().execute()
        counts["written"] += valid.count()
        counts["rejected"] += rejected.count()
        staged.unpersist()

    (spark.readStream.table(f"{catalog}.bronze.{spec.name}")
        .writeStream.foreachBatch(handle)
        .option("checkpointLocation", f"{CHECKPOINTS}/silver_{spec.name}")
        .trigger(availableNow=True)
        .start()
        .awaitTermination())
    return counts

# COMMAND ----------

# Dimensions first, so facts are validated against current dimension data.
summary = {spec.name: process(spec) for spec in SPECS}
for name, counts in summary.items():
    print(f"{name:10s} {counts}")

# COMMAND ----------

# MAGIC %md ### Quarantine overview

# COMMAND ----------

display(
    spark.table(table("_quarantine"))
    .groupBy("entity", "reason").count()
    .orderBy("entity", F.desc("count"))
)

# COMMAND ----------

# Hand the counts to downstream tasks (visible in the job run UI).
dbutils.jobs.taskValues.set(key="silver_counts", value=summary)
