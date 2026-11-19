"""RideOps as a Lakeflow Declarative Pipeline (formerly Delta Live Tables).

The same medallion flow as the notebooks, but declarative: you define *tables and rules*, and
the pipeline engine works out ordering, incremental processing, retries and checkpoints.

Expectations (data-quality rules attached to tables):
  @dlt.expect           record the violation in pipeline metrics, keep the row
  @dlt.expect_or_drop   drop the violating row (counted in metrics)
  @dlt.expect_or_fail   stop the update: used for rules that mean the data is unusable

STATUS: written for Databricks; cloud verification pending. Deployed by the bundle resource
`rideops_declarative` (databricks/resources/rideops_pipeline.yml).
"""

import dlt
from pyspark.sql import functions as F

LANDING = spark.conf.get("rideops.landing_path")  # noqa: F821 - provided by the pipeline runtime

ISO = "yyyy-MM-dd'T'HH:mm:ss'Z'"
LEGACY = "dd/MM/yyyy HH:mm"


def ts(col: str):
    return F.coalesce(F.try_to_timestamp(F.col(col), F.lit(ISO)),
                      F.try_to_timestamp(F.col(col), F.lit(LEGACY)))


def autoloader(entity: str, fmt: str):
    reader = (
        spark.readStream.format("cloudFiles")  # noqa: F821
        .option("cloudFiles.format", fmt)
        .option("cloudFiles.inferColumnTypes", "false")
        .option("cloudFiles.schemaEvolutionMode", "rescue")
    )
    if fmt == "csv":
        reader = reader.option("header", "true")
    return (
        reader.load(f"{LANDING}/{entity}/")
        .withColumn("_source_file", F.col("_metadata.file_path"))
        .withColumn("_ingested_at", F.current_timestamp())
    )


# ============================================================================ bronze


@dlt.table(comment="Raw bookings, all strings, as delivered", table_properties={"quality": "bronze"})
def bookings_bronze():
    return autoloader("bookings", "csv")


@dlt.table(comment="Raw customers", table_properties={"quality": "bronze"})
def customers_bronze():
    return autoloader("customers", "csv")


@dlt.table(comment="Raw payments", table_properties={"quality": "bronze"})
def payments_bronze():
    return autoloader("payments", "json")


# ============================================================================ silver

BOOKING_RULES = {
    "valid_booking_id": "booking_id RLIKE '^BKG-[0-9]{7}$'",
    "has_customer": "customer_id IS NOT NULL",
    "parseable_requested_at": "requested_at IS NOT NULL",
    "non_negative_fare": "fare_amount >= 0",
    "dropoff_after_pickup": "dropoff_at IS NULL OR dropoff_at >= pickup_at",
    "known_status": "status IN ('completed', 'cancelled', 'no_driver_found', 'in_progress')",
}


@dlt.view(comment="Typed and normalised bookings (not persisted)")
def bookings_typed():
    return dlt.read_stream("bookings_bronze").select(
        F.trim("booking_id").alias("booking_id"),
        F.nullif(F.trim("customer_id"), F.lit("")).alias("customer_id"),
        F.nullif(F.trim("driver_id"), F.lit("")).alias("driver_id"),
        F.upper(F.trim("city")).alias("city"),
        F.lower(F.trim("status")).alias("status"),
        ts("requested_at").alias("requested_at"),
        ts("pickup_at").alias("pickup_at"),
        ts("dropoff_at").alias("dropoff_at"),
        F.trim("fare_amount").try_cast("decimal(10,2)").alias("fare_amount"),
        F.lower(F.nullif(F.trim("payment_method"), F.lit(""))).alias("payment_method"),
        ts("updated_at").alias("updated_at"),
        "_source_file",
        "_ingested_at",
    )


@dlt.view
@dlt.expect_all_or_drop(BOOKING_RULES)
@dlt.expect("has_payment_method", "payment_method IS NOT NULL")  # warn only
def bookings_valid():
    return dlt.read_stream("bookings_typed").withColumn(
        "booking_date", F.to_date(F.from_utc_timestamp("requested_at", "Asia/Kolkata"))
    )


@dlt.table(comment="Bookings that failed at least one rule, with the rules they broke",
           table_properties={"quality": "quarantine"})
def bookings_quarantine():
    failed_rules = F.array_compact(F.array(*[
        F.when(~F.coalesce(F.expr(rule), F.lit(False)), F.lit(name))
        for name, rule in BOOKING_RULES.items()
    ]))
    return (
        dlt.read_stream("bookings_typed")
        .withColumn("failed_rules", failed_rules)
        .filter(F.size("failed_rules") > 0)
    )


# Latest version per booking (SCD type 1). sequence_by = updated_at means an older
# re-delivery can never overwrite a newer one, even if it arrives later.
dlt.create_streaming_table("bookings_silver", comment="Deduplicated bookings, latest version",
                           table_properties={"quality": "silver"})
dlt.apply_changes(
    target="bookings_silver",
    source="bookings_valid",
    keys=["booking_id"],
    sequence_by=F.col("updated_at"),
    stored_as_scd_type=1,
)


@dlt.view
@dlt.expect_or_drop("valid_customer_id", "customer_id RLIKE '^CUS-[0-9]{6}$'")
def customers_valid():
    return dlt.read_stream("customers_bronze").select(
        F.trim("customer_id").alias("customer_id"),
        F.nullif(F.trim("full_name"), F.lit("")).alias("full_name"),
        F.when(F.lower(F.trim("email")).rlike(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$"),
               F.lower(F.trim("email"))).alias("email"),
        F.upper(F.trim("city")).alias("city"),
        F.lower(F.trim("loyalty_tier")).alias("loyalty_tier"),
        ts("signup_ts").alias("signup_ts"),
        ts("updated_at").alias("updated_at"),
    )


# Full history of customer attribute changes (SCD type 2): every change of loyalty tier or
# city gets a new row with __START_AT / __END_AT, so reports can use the tier *at booking time*.
dlt.create_streaming_table("customers_history", comment="Customer changes over time (SCD2)")
dlt.apply_changes(
    target="customers_history",
    source="customers_valid",
    keys=["customer_id"],
    sequence_by=F.col("updated_at"),
    stored_as_scd_type=2,
    track_history_column_list=["loyalty_tier", "city", "email"],
)


@dlt.table(comment="Payments with type and reference checks")
@dlt.expect_or_drop("non_negative_amount", "amount >= 0")
@dlt.expect_or_drop("inr_only", "currency = 'INR'")
@dlt.expect_or_fail("payment_id_present", "payment_id IS NOT NULL")  # unusable data: stop
def payments_silver():
    return (
        dlt.read_stream("payments_bronze")
        .select(
            F.trim("payment_id").alias("payment_id"),
            F.trim("booking_id").alias("booking_id"),
            F.lower(F.trim("transaction_type")).alias("transaction_type"),
            F.trim("amount").try_cast("decimal(10,2)").alias("amount"),
            F.upper(F.trim("currency")).alias("currency"),
            F.lower(F.trim("status")).alias("status"),
            F.lower(F.trim("method")).alias("method"),
            ts("processed_at").alias("processed_at"),
        )
        .withWatermark("processed_at", "7 days")
        .dropDuplicatesWithinWatermark(["payment_id"])  # webhook re-deliveries
    )


# ============================================================================ gold


@dlt.table(comment="Bookings and revenue per business day and city",
           table_properties={"quality": "gold"})
def daily_city_summary():
    b = dlt.read("bookings_silver")
    return b.groupBy("booking_date", "city").agg(
        F.count("*").alias("total_bookings"),
        F.sum(F.when(F.col("status") == "completed", 1).otherwise(0)).alias("completed_rides"),
        F.sum(F.when(F.col("status") == "cancelled", 1).otherwise(0)).alias("cancelled_bookings"),
        F.sum("fare_amount").alias("gross_revenue"),
    )


@dlt.table(comment="Daily payment outcomes by method", table_properties={"quality": "gold"})
def payment_summary():
    p = dlt.read("payments_silver")
    charge = F.col("transaction_type") == "charge"
    return (
        p.withColumn("payment_date", F.to_date(F.from_utc_timestamp("processed_at", "Asia/Kolkata")))
        .groupBy("payment_date", "method")
        .agg(
            F.sum(F.when(charge, 1).otherwise(0)).alias("attempts"),
            F.sum(F.when(charge & (F.col("status") == "failed"), 1).otherwise(0)).alias("failed"),
            F.sum(F.when(charge & (F.col("status") == "success"), F.col("amount"))).alias("charged"),
        )
    )
