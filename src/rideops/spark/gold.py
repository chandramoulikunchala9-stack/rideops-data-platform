"""Gold: business aggregates from silver.

Incremental strategy:
  * daily_city_summary and cancellation_summary are keyed by booking_date. The job reads the
    Change Data Feed of silver.bookings from the last version it processed, finds the dates
    that changed and rewrites only those dates (`replaceWhere`). Untouched days are not
    recomputed.
  * Entity-level tables (drivers, customers, vehicles, payments) are small and are rebuilt
    in full, which is simpler and cheap at this size.
Definitions match sql/transform/30_analytics.sql so both pipelines can be reconciled.
"""

import logging

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from rideops.spark.lakehouse import BUSINESS_TZ, Lakehouse, parse_ts, table_exists

logger = logging.getLogger(__name__)

STATE_NAME = "gold_bookings_cdf"


def _count_if(cond: F.Column) -> F.Column:
    return F.sum(F.when(cond, 1).otherwise(0)).cast("int")


def _minutes(end: str, start: str) -> F.Column:
    return (F.unix_timestamp(end) - F.unix_timestamp(start)) / 60


# =============================================================================== aggregations


def daily_city_summary(bookings: DataFrame) -> DataFrame:
    completed = F.col("status") == "completed"
    total = F.count("*")
    return bookings.groupBy("booking_date", "city").agg(
        total.cast("int").alias("total_bookings"),
        _count_if(completed).alias("completed_rides"),
        _count_if(F.col("status") == "cancelled").alias("cancelled_bookings"),
        _count_if(F.col("status") == "no_driver_found").alias("no_driver_bookings"),
        F.sum("fare_amount").cast("decimal(14,2)").alias("gross_revenue"),
        F.round(F.avg(F.when(completed, F.col("fare_amount"))), 2).alias("avg_fare"),
        F.round(F.avg(F.when(completed, F.col("distance_km"))), 2).alias("avg_distance_km"),
        F.round(F.avg(_minutes("pickup_at", "requested_at")), 2).alias("avg_wait_min"),
        F.countDistinct(F.when(completed, F.col("driver_id"))).alias("active_drivers"),
        F.countDistinct("customer_id").alias("active_customers"),
        F.round(_count_if(F.col("status") == "cancelled") / total, 4).alias("cancellation_rate"),
    )


def cancellation_summary(bookings: DataFrame) -> DataFrame:
    return (
        bookings.filter(F.col("status") == "cancelled")
        .groupBy("booking_date", "city", "cancelled_by", "cancellation_reason")
        .agg(F.count("*").cast("int").alias("cancellations"),
             F.sum("fare_amount").cast("decimal(12,2)").alias("fees_collected"))
    )  # fmt: skip


def driver_performance(bookings: DataFrame, drivers: DataFrame) -> DataFrame:
    completed = F.col("status") == "completed"
    per_driver = (
        bookings.filter(F.col("driver_id").isNotNull())
        .groupBy("driver_id")
        .agg(
            F.count("*").cast("int").alias("total_assigned"),
            _count_if(completed).alias("completed_rides"),
            _count_if(F.col("cancelled_by") == "driver").alias("driver_cancellations"),
            F.coalesce(F.sum(F.when(completed, F.col("fare_amount"))), F.lit(0)).alias("total_revenue"),
            F.coalesce(F.sum(F.when(completed, F.col("distance_km"))), F.lit(0)).alias("total_distance_km"),
            F.countDistinct(F.when(completed, F.col("booking_date"))).alias("active_days"),
            F.max("dropoff_at").alias("last_ride_at"),
        )
    )  # fmt: skip
    joined = per_driver.join(drivers.select("driver_id", "city", "rating"), "driver_id", "left")
    rank_window = Window.partitionBy("city").orderBy(F.col("total_revenue").desc())
    return joined.select(
        "driver_id", "city", "rating", "total_assigned", "completed_rides", "driver_cancellations",
        F.round(F.col("completed_rides") / F.col("total_assigned"), 4).alias("completion_rate"),
        "total_revenue", "total_distance_km", "active_days",
        F.rank().over(rank_window).alias("revenue_rank_in_city"),
        "last_ride_at",
    )  # fmt: skip


def customer_activity(bookings: DataFrame, customers: DataFrame) -> DataFrame:
    by_time = Window.partitionBy("customer_id").orderBy("requested_at")
    rides = bookings.withColumn(
        "gap_sec", F.unix_timestamp("requested_at") - F.unix_timestamp(F.lag("requested_at").over(by_time))
    )  # fmt: skip
    per_customer = rides.groupBy("customer_id").agg(
        F.min("requested_at").alias("first_booking_at"),
        F.max("requested_at").alias("last_booking_at"),
        F.count("*").cast("int").alias("total_bookings"),
        _count_if(F.col("status") == "completed").alias("completed_rides"),
        F.sum("fare_amount").alias("total_spend"),
        F.round(F.avg("gap_sec") / 86400, 2).alias("avg_days_between_rides"),
    )
    as_of = bookings.agg(F.max("requested_at").alias("as_of"))
    days_before = lambda d: F.col("as_of") - F.expr(f"INTERVAL {d} DAYS")  # noqa: E731
    return (
        per_customer.join(customers.select("customer_id", "city", "loyalty_tier"), "customer_id")
        .crossJoin(as_of)
        .withColumn(
            "segment",
            F.when(F.col("last_booking_at") < days_before(21), "at_risk")
            .when(F.col("total_bookings") >= 20, "power_user")
            .when(F.col("first_booking_at") >= days_before(14), "new")
            .otherwise("regular"),
        )
        .drop("as_of")
    )


def payment_summary(payments: DataFrame) -> DataFrame:
    charge = F.col("transaction_type") == "charge"
    attempts = _count_if(charge)
    failed = _count_if(charge & (F.col("status") == "failed"))
    return (
        payments.withColumn("payment_date", F.to_date(F.from_utc_timestamp("processed_at", BUSINESS_TZ)))
        .withColumn("method", F.coalesce("method", F.lit("unknown")))
        .groupBy("payment_date", "method")
        .agg(
            attempts.alias("attempts"),
            _count_if(charge & (F.col("status") == "success")).alias("successful_charges"),
            failed.alias("failed_attempts"),
            _count_if(F.col("transaction_type") == "refund").alias("refunds"),
            F.coalesce(F.sum(F.when(charge & (F.col("status") == "success"), F.col("amount"))), F.lit(0)).alias("charged_amount"),
            F.coalesce(F.sum(F.when(F.col("transaction_type") == "refund", F.col("amount"))), F.lit(0)).alias("refunded_amount"),
            F.round(failed / F.nullif(attempts, F.lit(0)), 4).alias("failure_rate"),
        )
        .filter(F.col("attempts") > 0)
    )  # fmt: skip


def vehicle_utilization(bookings: DataFrame, vehicles: DataFrame) -> DataFrame:
    rides = (
        bookings.filter((F.col("status") == "completed") & F.col("vehicle_id").isNotNull())
        .groupBy("vehicle_id")
        .agg(
            F.countDistinct("driver_id").alias("drivers_sharing"),
            F.count("*").cast("int").alias("completed_rides"),
            F.coalesce(F.sum("duration_min") / 60, F.lit(0)).alias("ride_hours"),
            F.countDistinct("booking_date").alias("active_days"),
        )
    )
    return (
        vehicles.select("vehicle_id", "vehicle_type", "city")
        .join(rides, "vehicle_id", "left")
        .fillna(0, ["drivers_sharing", "completed_rides", "active_days"])
        .withColumn("ride_hours", F.round(F.coalesce("ride_hours", F.lit(0)), 2))
        .withColumn(
            "utilization_rate",
            F.round(F.coalesce(F.col("ride_hours") / F.nullif(F.col("active_days") * 12, F.lit(0)), F.lit(0)), 4),
        )
    )  # fmt: skip


def app_funnel_daily(app_events: DataFrame) -> DataFrame:
    """Daily conversion funnel from bronze app events (deduplicated by event_id)."""
    events = (
        app_events.dropDuplicates(["event_id"])
        .withColumn("event_ts", parse_ts("event_ts"))
        .filter(F.col("event_ts").isNotNull())
        .withColumn("event_date", F.to_date(F.from_utc_timestamp("event_ts", BUSINESS_TZ)))
        .withColumn("platform", F.lower(F.trim("platform")))
    )
    sessions = events.groupBy("event_date", "session_id").agg(
        F.first("platform", ignorenulls=True).alias("platform"),
        F.max(F.when(F.col("event_type") == "fare_estimate_viewed", 1).otherwise(0)).alias("saw_fare"),
        F.max(F.when(F.col("event_type") == "booking_requested", 1).otherwise(0)).alias("booked"),
        F.max(F.when(F.col("event_type") == "app_crashed", 1).otherwise(0)).alias("crashed"),
    )  # fmt: skip
    return sessions.groupBy("event_date", F.coalesce("platform", F.lit("unknown")).alias("platform")).agg(
        F.count("*").cast("int").alias("sessions"),
        F.sum("saw_fare").cast("int").alias("fare_views"),
        F.sum("booked").cast("int").alias("bookings"),
        F.sum("crashed").cast("int").alias("crashes"),
        F.round(F.sum("booked") / F.count("*"), 4).alias("conversion_rate"),
    )  # fmt: skip


# =============================================================================== job


def _read(spark: SparkSession, path: str) -> DataFrame:
    return spark.read.format("delta").load(path)


def changed_booking_dates(spark: SparkSession, lake: Lakehouse) -> tuple[list | None, int]:
    """Dates touched since the last gold run (None = first run, rebuild everything)."""
    path = lake.silver("bookings")
    latest = DeltaTable.forPath(spark, path).history(1).collect()[0]["version"]
    last = lake.read_state(STATE_NAME).get("last_version")
    if last is None or not table_exists(spark, lake.gold("daily_city_summary")):
        return None, latest
    if last >= latest:
        return [], latest
    changes = (
        spark.read.format("delta")
        .option("readChangeFeed", "true")
        .option("startingVersion", last + 1)
        .load(path)
        .select("booking_date")
        .distinct()
    )
    return sorted(r["booking_date"] for r in changes.collect() if r["booking_date"]), latest


def _write_dates(df: DataFrame, path: str, dates: list | None) -> None:
    writer = df.write.format("delta").mode("overwrite")
    if dates is None:
        writer.option("overwriteSchema", "true").save(path)
        return
    in_list = ", ".join(f"DATE'{d.isoformat()}'" for d in dates)
    writer.option("replaceWhere", f"booking_date IN ({in_list})").save(path)


def build_gold(spark: SparkSession, lake: Lakehouse) -> dict[str, int]:
    bookings = _read(spark, lake.silver("bookings"))
    dates, latest_version = changed_booking_dates(spark, lake)
    rows_written = 0

    if dates is None or dates:
        scope = bookings if dates is None else bookings.filter(F.col("booking_date").isin(dates))
        for name, builder in (("daily_city_summary", daily_city_summary),
                              ("cancellation_summary", cancellation_summary)):  # fmt: skip
            df = builder(scope)
            _write_dates(df, lake.gold(name), dates)
            rows_written += df.count()
        logger.info(
            "gold daily tables: %s", "full rebuild" if dates is None else f"{len(dates)} date(s)"
        )
    else:
        logger.info("gold daily tables: no silver changes since version %s", latest_version)

    full = {
        "driver_performance": lambda: driver_performance(bookings, _read(spark, lake.silver("drivers"))),
        "customer_activity": lambda: customer_activity(bookings, _read(spark, lake.silver("customers"))),
        "payment_summary": lambda: payment_summary(_read(spark, lake.silver("payments"))),
        "vehicle_utilization": lambda: vehicle_utilization(bookings, _read(spark, lake.silver("vehicles"))),
    }  # fmt: skip
    if table_exists(spark, lake.bronze("app_events")):
        full["app_funnel_daily"] = lambda: app_funnel_daily(_read(spark, lake.bronze("app_events")))
    for name, build in full.items():
        df = build()
        df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(
            lake.gold(name)
        )
        rows_written += df.count()

    lake.write_state(STATE_NAME, {"last_version": latest_version})
    return {"read": bookings.count(), "written": rows_written}
