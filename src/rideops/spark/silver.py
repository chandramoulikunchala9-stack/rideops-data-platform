"""Silver: bronze -> cleaned, validated, deduplicated Delta tables.

The business rules mirror sql/transform/10_core_dimensions.sql and 20_core_facts.sql so that the
Spark lakehouse and the SQL warehouse can be reconciled against each other:
  * rows that cannot be trusted (bad key, unparseable timestamp, negative amount, orphan
    reference) go to silver/_quarantine with a reason;
  * field-level problems are repaired (value -> NULL / standardised) and listed in `dq_flags`;
  * duplicates collapse to the latest version per key (ROW_NUMBER over updated_at).

Each entity is processed as a stream over its bronze Delta table with foreachBatch:
every micro-batch is transformed, quarantined rows are appended, and valid rows are MERGEd.
The checkpoint makes this incremental: a re-run only sees bronze rows added since last time.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from rideops.generators.reference import CITIES, VEHICLE_TYPES
from rideops.spark.lakehouse import (
    Lakehouse,
    blank_to_null,
    business_date,
    flags,
    is_legacy_ts,
    parse_ts,
    table_exists,
    to_num,
)

logger = logging.getLogger(__name__)

BOOKING_STATUSES = ["completed", "cancelled", "no_driver_found", "in_progress"]
METADATA = ["_source_file", "_ingested_at"]


@dataclass(frozen=True)
class References:
    """Lookup data needed to validate an entity (current silver state + reference tables)."""

    cities: DataFrame
    vehicle_types: DataFrame
    vehicles: DataFrame | None = None
    drivers: DataFrame | None = None
    customers: DataFrame | None = None
    bookings: DataFrame | None = None


def load_references(spark: SparkSession, lake: Lakehouse) -> References:
    def current(entity: str, key: str) -> DataFrame | None:
        path = lake.silver(entity)
        return (
            spark.read.format("delta").load(path).select(key) if table_exists(spark, path) else None
        )

    return References(
        cities=spark.createDataFrame([(c.code,) for c in CITIES], "city_code string"),
        vehicle_types=spark.createDataFrame([(t.name,) for t in VEHICLE_TYPES], "vehicle_type string"),
        vehicles=current("vehicles", "vehicle_id"),
        drivers=current("drivers", "driver_id"),
        customers=current("customers", "customer_id"),
        bookings=current("bookings", "booking_id"),
    )  # fmt: skip


def _raw_json(df: DataFrame) -> F.Column:
    cols = [c for c in df.columns if not c.startswith("_")]
    return F.to_json(F.struct(*cols))


def _known(df: DataFrame, ref: DataFrame | None, left: str, right: str, alias: str) -> DataFrame:
    """Left-join a reference key; `alias` is NULL when the value is unknown."""
    if ref is None:
        return df.withColumn(alias, F.lit(None).cast("string"))
    return df.join(
        F.broadcast(ref.select(F.col(right).alias(alias))), F.col(left) == F.col(alias), "left"
    )


# =============================================================================== transforms


def transform_vehicles(df: DataFrame, refs: References) -> DataFrame:
    max_year = F.year(F.current_date()) + 1
    t = df.select(
        F.trim("vehicle_id").alias("vehicle_id"),
        F.trim("registration_number").alias("registration_number"),
        F.lower(F.trim("vehicle_type")).alias("vehicle_type"),
        blank_to_null("make").alias("make"),
        blank_to_null("model").alias("model"),
        to_num("manufacture_year", "int").alias("_year"),
        to_num("seats", "int").alias("_seats"),
        F.upper(F.trim("city")).alias("_city"),
        (F.lower(F.trim("is_active")) == "true").alias("is_active"),
        parse_ts("updated_at").alias("updated_at"),
        _raw_json(df).alias("_raw_record"),
        *METADATA,
    )
    t = _known(t, refs.cities, "_city", "city_code", "city")
    t = _known(t, refs.vehicle_types, "vehicle_type", "vehicle_type", "_known_type")
    year_ok = F.col("_year").between(1990, max_year)
    return t.select(
        "*",
        F.when(year_ok, F.col("_year")).alias("manufacture_year"),
        F.when(F.col("_seats") > 0, F.col("_seats")).alias("seats"),
        flags(
            (F.col("_year").isNotNull() & ~year_ok, "invalid_manufacture_year"),
            (F.col("_seats").isNotNull() & (F.col("_seats") <= 0), "invalid_seats"),
            (F.col("make").isNull() | F.col("model").isNull(), "missing_make_or_model"),
            (F.col("_year").isNull(), "missing_manufacture_year"),
        ).alias("dq_flags"),
        F.when(~F.coalesce(F.col("vehicle_id").rlike(r"^VEH-\d{6}$"), F.lit(False)), "invalid vehicle_id")
        .when(F.col("updated_at").isNull(), "unparseable updated_at")
        .when(F.col("_known_type").isNull(), "unknown vehicle_type")
        .alias("_reject_reason"),
    )  # fmt: skip


def transform_drivers(df: DataFrame, refs: References) -> DataFrame:
    t = df.select(
        F.trim("driver_id").alias("driver_id"),
        blank_to_null("full_name").alias("full_name"),
        blank_to_null("phone").alias("phone"),
        F.upper(F.trim("city")).alias("_city"),
        blank_to_null("license_number").alias("license_number"),
        blank_to_null("vehicle_id").alias("_vehicle_id"),
        to_num("rating", "decimal(3,2)").alias("_rating"),
        F.lower(F.trim("status")).alias("status"),
        parse_ts("joined_at").alias("joined_at"),
        parse_ts("updated_at").alias("updated_at"),
        _raw_json(df).alias("_raw_record"),
        *METADATA,
    )
    t = _known(t, refs.cities, "_city", "city_code", "city")
    t = _known(t, refs.vehicles, "_vehicle_id", "vehicle_id", "vehicle_id")
    rating_ok = F.col("_rating").between(1, 5)
    return t.select(
        "*",
        F.when(rating_ok, F.col("_rating")).alias("rating"),
        flags(
            (F.col("_rating").isNotNull() & ~rating_ok, "rating_out_of_range"),
            (F.col("_rating").isNull(), "missing_rating"),
            (F.col("license_number").isNull(), "missing_license_number"),
            (F.col("_vehicle_id").isNotNull() & F.col("vehicle_id").isNull(), "unknown_vehicle_id"),
            (F.col("_vehicle_id").isNull(), "missing_vehicle_id"),
            (F.col("phone").isNull(), "missing_phone"),
        ).alias("dq_flags"),
        F.when(~F.coalesce(F.col("driver_id").rlike(r"^DRV-\d{6}$"), F.lit(False)), "invalid driver_id")
        .when(F.col("joined_at").isNull() | F.col("updated_at").isNull(), "unparseable timestamp")
        .when(~F.col("status").isin("active", "inactive", "suspended"), "invalid status")
        .alias("_reject_reason"),
    )  # fmt: skip


EMAIL_REGEX = r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$"


def transform_customers(df: DataFrame, refs: References) -> DataFrame:
    t = df.select(
        F.trim("customer_id").alias("customer_id"),
        blank_to_null("full_name").alias("full_name"),
        F.lower(blank_to_null("email")).alias("_email"),
        blank_to_null("phone").alias("phone"),
        F.upper(F.trim("city")).alias("_city"),
        F.lower(blank_to_null("loyalty_tier")).alias("loyalty_tier"),
        parse_ts("signup_ts").alias("signup_ts"),
        is_legacy_ts("signup_ts").alias("_signup_legacy"),
        parse_ts("updated_at").alias("updated_at"),
        _raw_json(df).alias("_raw_record"),
        *METADATA,
    )
    t = _known(t, refs.cities, "_city", "city_code", "city")
    email_ok = F.coalesce(F.col("_email").rlike(EMAIL_REGEX), F.lit(False))
    return t.select(
        "*",
        F.when(email_ok, F.col("_email")).alias("email"),
        flags(
            (F.col("_email").isNotNull() & ~email_ok, "malformed_email"),
            (F.col("_email").isNull(), "missing_email"),
            (F.col("phone").isNull(), "missing_phone"),
            (F.col("loyalty_tier").isNull(), "missing_loyalty_tier"),
            (F.col("city").isNull(), "unknown_city"),
            (F.col("_signup_legacy"), "legacy_timestamp_standardised"),
        ).alias("dq_flags"),
        F.when(~F.coalesce(F.col("customer_id").rlike(r"^CUS-\d{6}$"), F.lit(False)), "invalid customer_id")
        .when(F.col("signup_ts").isNull() | F.col("updated_at").isNull(), "unparseable timestamp")
        .alias("_reject_reason"),
    )  # fmt: skip


def transform_bookings(df: DataFrame, refs: References) -> DataFrame:
    t = df.select(
        F.trim("booking_id").alias("booking_id"),
        blank_to_null("customer_id").alias("customer_id"),
        blank_to_null("driver_id").alias("driver_id"),
        blank_to_null("vehicle_id").alias("vehicle_id"),
        F.upper(F.trim("city")).alias("_city"),
        F.lower(blank_to_null("vehicle_type")).alias("vehicle_type"),
        F.lower(F.trim("status")).alias("status"),
        (F.col("status") != F.lower(F.trim("status"))).alias("_status_fixed"),
        parse_ts("requested_at").alias("requested_at"),
        is_legacy_ts("requested_at").alias("_requested_legacy"),
        *[parse_ts(c).alias(c) for c in ("accepted_at", "pickup_at", "dropoff_at", "cancelled_at")],
        blank_to_null("cancelled_by").alias("cancelled_by"),
        blank_to_null("cancellation_reason").alias("cancellation_reason"),
        to_num("distance_km", "decimal(7,2)").alias("distance_km"),
        to_num("duration_min", "decimal(7,1)").alias("duration_min"),
        to_num("surge_multiplier", "decimal(4,2)").alias("surge_multiplier"),
        to_num("fare_amount", "decimal(10,2)").alias("fare_amount"),
        F.upper(F.trim("currency")).alias("currency"),
        F.lower(blank_to_null("payment_method")).alias("payment_method"),
        parse_ts("updated_at").alias("updated_at"),
        _raw_json(df).alias("_raw_record"),
        *METADATA,
    )
    t = _known(t, refs.cities, "_city", "city_code", "city")
    t = _known(t, refs.customers, "customer_id", "customer_id", "_known_customer")
    t = _known(t, refs.drivers, "driver_id", "driver_id", "_known_driver")
    return t.select(
        "*",
        business_date("requested_at").alias("booking_date"),
        flags(
            (F.col("_requested_legacy"), "legacy_timestamp_standardised"),
            (F.coalesce(F.col("_status_fixed"), F.lit(False)), "status_normalised"),
            (F.col("driver_id").isNotNull() & F.col("_known_driver").isNull(), "unknown_driver_id"),
            (F.col("payment_method").isNull(), "missing_payment_method"),
            (F.col("distance_km").isNull(), "missing_distance"),
            (F.col("surge_multiplier").isNull(), "missing_surge_multiplier"),
        ).alias("dq_flags"),
        F.when(~F.coalesce(F.col("booking_id").rlike(r"^BKG-\d{7}$"), F.lit(False)), "invalid booking_id")
        .when(F.col("requested_at").isNull() | F.col("updated_at").isNull(), "unparseable timestamp")
        .when(F.col("customer_id").isNull(), "missing customer_id")
        .when(F.col("_known_customer").isNull(), "unknown customer_id")
        .when(F.col("city").isNull(), "unknown city")
        .when(~F.col("status").isin(*BOOKING_STATUSES), "invalid status")
        .when(F.col("fare_amount").isNull() | (F.col("fare_amount") < 0), "invalid fare_amount")
        .when(F.col("dropoff_at") < F.col("pickup_at"), "dropoff before pickup")
        .alias("_reject_reason"),
    )  # fmt: skip


def transform_payments(df: DataFrame, refs: References) -> DataFrame:
    t = df.select(
        F.trim("payment_id").alias("payment_id"),
        F.trim("booking_id").alias("booking_id"),
        blank_to_null("customer_id").alias("customer_id"),
        F.lower(F.trim("transaction_type")).alias("transaction_type"),
        to_num("attempt_number", "int").alias("attempt_number"),
        to_num("amount", "decimal(10,2)").alias("amount"),
        F.upper(F.trim("currency")).alias("currency"),
        F.lower(blank_to_null("method")).alias("method"),
        blank_to_null("gateway").alias("gateway"),
        F.lower(F.trim("status")).alias("status"),
        blank_to_null("failure_reason").alias("failure_reason"),
        parse_ts("processed_at").alias("processed_at"),
        # payments are immutable events; updated_at = processed_at for versioning
        parse_ts("processed_at").alias("updated_at"),
        _raw_json(df).alias("_raw_record"),
        *METADATA,
    )
    t = _known(t, refs.bookings, "booking_id", "booking_id", "_known_booking")
    return t.select(
        "*",
        flags(
            (F.col("method").isNull(), "missing_method"),
            (F.col("gateway").isNull(), "missing_gateway"),
        ).alias("dq_flags"),
        F.when(~F.coalesce(F.col("payment_id").rlike(r"^PAY-\d{8}$"), F.lit(False)), "invalid payment_id")
        .when(F.col("processed_at").isNull(), "unparseable processed_at")
        .when(F.col("_known_booking").isNull(), "unknown booking_id")
        .when(F.col("amount").isNull() | (F.col("amount") < 0), "invalid amount")
        .when(F.col("currency") != "INR", "unsupported currency")
        .when(~F.col("transaction_type").isin("charge", "refund"), "invalid transaction_type")
        .when(~F.col("status").isin("success", "failed"), "invalid status")
        .alias("_reject_reason"),
    )  # fmt: skip


@dataclass(frozen=True)
class EntitySpec:
    name: str
    key: str
    transform: Callable[[DataFrame, References], DataFrame]
    columns: tuple[str, ...]
    upsert: bool = True  # False: insert-only (immutable events)


SPECS: tuple[EntitySpec, ...] = (
    EntitySpec("vehicles", "vehicle_id", transform_vehicles, (
        "vehicle_id", "registration_number", "vehicle_type", "make", "model", "manufacture_year",
        "seats", "city", "is_active", "updated_at", "dq_flags", "_ingested_at")),
    EntitySpec("drivers", "driver_id", transform_drivers, (
        "driver_id", "full_name", "phone", "city", "license_number", "vehicle_id", "rating",
        "status", "joined_at", "updated_at", "dq_flags", "_ingested_at")),
    EntitySpec("customers", "customer_id", transform_customers, (
        "customer_id", "full_name", "email", "phone", "city", "loyalty_tier", "signup_ts",
        "updated_at", "dq_flags", "_ingested_at")),
    EntitySpec("bookings", "booking_id", transform_bookings, (
        "booking_id", "customer_id", "driver_id", "vehicle_id", "city", "vehicle_type", "status",
        "requested_at", "accepted_at", "pickup_at", "dropoff_at", "cancelled_at", "cancelled_by",
        "cancellation_reason", "distance_km", "duration_min", "surge_multiplier", "fare_amount",
        "currency", "payment_method", "updated_at", "booking_date", "dq_flags", "_ingested_at")),
    EntitySpec("payments", "payment_id", transform_payments, (
        "payment_id", "booking_id", "customer_id", "transaction_type", "attempt_number", "amount",
        "currency", "method", "gateway", "status", "failure_reason", "processed_at", "dq_flags",
        "_ingested_at"), upsert=False),
)  # fmt: skip
SPEC_BY_NAME = {s.name: s for s in SPECS}


def latest_versions(df: DataFrame, key: str) -> DataFrame:
    """Keep one row per key: the newest updated_at, ties broken by the latest ingestion."""
    window = Window.partitionBy(key).orderBy(
        F.col("updated_at").desc_nulls_last(), F.col("_ingested_at").desc()
    )
    return df.withColumn("_rank", F.row_number().over(window)).filter("_rank = 1").drop("_rank")


def split_valid_rejected(staged: DataFrame, spec: EntitySpec) -> tuple[DataFrame, DataFrame]:
    first = latest_versions(staged, spec.key)
    valid = first.filter(F.col("_reject_reason").isNull()).select(*spec.columns)
    rejected = first.filter(F.col("_reject_reason").isNotNull()).select(
        F.lit(spec.name).alias("entity"),
        F.col(spec.key).alias("record_key"),
        F.col("_reject_reason").alias("reason"),
        F.col("_raw_record").alias("raw_record"),
        F.col("_source_file").alias("source_file"),
        F.current_timestamp().alias("rejected_at"),
    )
    return valid, rejected


def _merge(spark: SparkSession, path: str, valid: DataFrame, spec: EntitySpec) -> None:
    if not table_exists(spark, path):
        valid.limit(0).write.format("delta").save(path)  # create with the right schema
    target = DeltaTable.forPath(spark, path).alias("t")
    merge = target.merge(valid.alias("s"), f"t.{spec.key} = s.{spec.key}")
    if spec.upsert:  # an older re-delivery never overwrites newer data
        merge = merge.whenMatchedUpdateAll(condition="s.updated_at > t.updated_at")
    merge.whenNotMatchedInsertAll().execute()


def process_entity(spark: SparkSession, lake: Lakehouse, spec: EntitySpec) -> dict[str, int]:
    """Stream new bronze rows of one entity into silver. Returns read/written/rejected counts."""
    counts = {"read": 0, "written": 0, "rejected": 0}

    def handle_batch(batch: DataFrame, batch_id: int) -> None:
        staged = spec.transform(batch, load_references(spark, lake)).persist()
        try:
            valid, rejected = split_valid_rejected(staged, spec)
            n_rejected = rejected.count()
            if n_rejected:
                rejected.write.format("delta").mode("append").save(lake.quarantine)
            n_valid = valid.count()
            _merge(spark, lake.silver(spec.name), valid, spec)
            counts["read"] += staged.count()
            counts["written"] += n_valid
            counts["rejected"] += n_rejected
        finally:
            staged.unpersist()

    query = (
        spark.readStream.format("delta").load(lake.bronze(spec.name))
        .writeStream.foreachBatch(handle_batch)
        .option("checkpointLocation", lake.checkpoint(f"silver_{spec.name}"))
        .trigger(availableNow=True)
        .queryName(f"silver_{spec.name}")
        .start()
    )  # fmt: skip
    query.awaitTermination()
    logger.info("silver %-10s %s", spec.name, counts)
    return counts


def process_all(spark: SparkSession, lake: Lakehouse) -> dict[str, int]:
    """Dimensions first, so facts can be validated against them."""
    totals = {"read": 0, "written": 0, "rejected": 0}
    for spec in SPECS:
        for key, value in process_entity(spark, lake, spec).items():
            totals[key] += value
    return totals
