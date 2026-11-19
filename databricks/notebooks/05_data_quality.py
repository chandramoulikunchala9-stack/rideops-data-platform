# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Data-quality gate
# MAGIC
# MAGIC Runs the lakehouse expectations on silver. If any check **FAIL**s, the notebook raises, the
# MAGIC task fails, and the `gold_aggregations` task (which depends on this one) does **not** run,
# MAGIC so bad data never reaches dashboards. Results are appended to `ops.data_quality_results`.
# MAGIC
# MAGIC **Status:** written for Databricks; cloud verification pending.

# COMMAND ----------

dbutils.widgets.text("catalog", "rideops_dev")
catalog = dbutils.widgets.get("catalog")

# COMMAND ----------

from datetime import datetime, timezone

from pyspark.sql import functions as F

from rideops.quality.checks import CheckResult, decide_status
from rideops.spark.quality import EXPECTATIONS, evaluate

results: list[CheckResult] = []
for exp in EXPECTATIONS:
    failed, total = evaluate(spark.table(f"{catalog}.silver.{exp.entity}"), exp)
    results.append(CheckResult(exp.name, exp.entity, exp.check_type, failed, total, exp.threshold,
                               {}, decide_status(failed, total, exp.threshold)))

checked_at = datetime.now(timezone.utc)
df = spark.createDataFrame(
    [(r.check_name, r.entity, r.check_type, r.status, r.failed_rows, r.total_rows, r.threshold,
      checked_at) for r in results],
    "check_name string, entity string, check_type string, status string, failed_rows long, "
    "total_rows long, threshold double, checked_at timestamp",
)
df.write.mode("append").saveAsTable(f"{catalog}.ops.data_quality_results")
display(df.orderBy(F.desc("status")))

# COMMAND ----------

failures = [r.check_name for r in results if r.status == "FAIL"]
if failures:
    raise RuntimeError(f"Data-quality gate failed: {failures}. Gold refresh is blocked.")
print("Data-quality gate passed.")
