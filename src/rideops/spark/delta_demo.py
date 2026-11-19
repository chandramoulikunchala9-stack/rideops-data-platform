"""A guided, verifiable tour of Delta Lake features on a scratch table.

Run:  docker compose run --rm spark python -m rideops.spark delta-demo

Each step prints what happened and returns facts that tests assert on:
  1. ACID write          a failed write leaves the table unchanged
  2. Schema enforcement  appending a mismatched schema is rejected
  3. Schema evolution    mergeSchema adds a new column; old rows get NULL
  4. MERGE (upsert)      update matching rows, insert new ones, in one transaction
  5. Time travel         read version 0 after later changes
  6. DESCRIBE HISTORY    the transaction log as an audit trail
  7. OPTIMIZE + ZORDER   compact small files and co-locate rows by a column
  8. VACUUM             delete files no longer referenced (retention 0 for the demo only)
"""

import logging
import shutil

from delta.tables import DeltaTable
from pyspark.errors import AnalysisException
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

logger = logging.getLogger(__name__)


def run_demo(spark: SparkSession, path: str) -> dict:
    shutil.rmtree(path, ignore_errors=True)
    facts: dict = {}
    say = lambda msg: logger.info("delta-demo | %s", msg)  # noqa: E731

    # 1. ACID: write 6 small files, then attempt a write that fails mid-way.
    base = spark.createDataFrame(
        [(i, f"CITY{i % 3}", float(i * 10)) for i in range(1, 7)],
        "id int, city string, fare double",
    )
    base.repartition(6).write.format("delta").save(path)
    try:
        bad = base.withColumn(
            "fare", F.expr("CASE WHEN id = 4 THEN raise_error('boom') ELSE fare END")
        )
        bad.write.format("delta").mode("append").save(path)
    except Exception:  # noqa: BLE001 - the failure is the point of the demo
        pass
    facts["rows_after_failed_write"] = spark.read.format("delta").load(path).count()
    say(f"1. ACID: failed append left {facts['rows_after_failed_write']} rows (no partial data)")

    # 2. Schema enforcement
    try:
        spark.createDataFrame(
            [(7, "X", "not-a-number")], "id int, city string, fare string"
        ).write.format("delta").mode("append").save(path)
        facts["schema_enforced"] = False
    except AnalysisException as exc:
        facts["schema_enforced"] = True
        say(f"2. Schema enforcement rejected the write: {str(exc).splitlines()[0][:100]}")

    # 3. Schema evolution
    spark.createDataFrame(
        [(7, "CITY1", 70.0, "upi")], "id int, city string, fare double, method string"
    ).write.format("delta").mode("append").option("mergeSchema", "true").save(path)
    table = spark.read.format("delta").load(path)
    facts["columns_after_evolution"] = table.columns
    facts["null_methods_for_old_rows"] = table.filter(F.col("method").isNull()).count()
    say(f"3. Schema evolution: columns now {table.columns}")

    # 4. MERGE
    updates = spark.createDataFrame(
        [(1, "CITY1", 999.0, "card"), (8, "CITY2", 80.0, "cash")],
        "id int, city string, fare double, method string",
    )
    (DeltaTable.forPath(spark, path).alias("t")
        .merge(updates.alias("s"), "t.id = s.id")
        .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute())  # fmt: skip
    facts["fare_of_id_1"] = spark.read.format("delta").load(path).filter("id = 1").first()["fare"]
    facts["rows_after_merge"] = spark.read.format("delta").load(path).count()
    say(f"4. MERGE: id 1 fare -> {facts['fare_of_id_1']}, rows -> {facts['rows_after_merge']}")

    # 5. Time travel
    v0 = spark.read.format("delta").option("versionAsOf", 0).load(path)
    facts["rows_at_version_0"] = v0.count()
    facts["fare_of_id_1_at_v0"] = v0.filter("id = 1").first()["fare"]
    say(
        f"5. Time travel: version 0 had {facts['rows_at_version_0']} rows, id 1 fare {facts['fare_of_id_1_at_v0']}"
    )

    # 6. History
    history = DeltaTable.forPath(spark, path).history()
    facts["operations"] = [r["operation"] for r in history.orderBy("version").collect()]
    say(f"6. History: {facts['operations']}")

    # 7. OPTIMIZE + ZORDER
    files_before = spark.sql(f"DESCRIBE DETAIL delta.`{path}`").first()["numFiles"]
    spark.sql(f"OPTIMIZE delta.`{path}` ZORDER BY (city)")
    files_after = spark.sql(f"DESCRIBE DETAIL delta.`{path}`").first()["numFiles"]
    facts["files_before_optimize"], facts["files_after_optimize"] = files_before, files_after
    say(f"7. OPTIMIZE ZORDER BY city: {files_before} files -> {files_after}")

    # 8. VACUUM (0 hours only because this is a scratch table; default retention is 7 days)
    spark.sql(f"VACUUM delta.`{path}` RETAIN 0 HOURS")
    try:
        # collect(), not count(): Delta can answer count() from log statistics without
        # touching data files, which would hide that the files are gone.
        spark.read.format("delta").option("versionAsOf", 0).load(path).collect()
        facts["time_travel_after_vacuum"] = True
    except Exception:  # noqa: BLE001
        facts["time_travel_after_vacuum"] = False
    say(f"8. VACUUM: time travel to v0 still possible? {facts['time_travel_after_vacuum']} "
        "(old files deleted, so history beyond retention is gone)")  # fmt: skip
    return facts
