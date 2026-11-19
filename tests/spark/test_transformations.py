"""Pure DataFrame transformation tests: no files, no streaming, small hand-written inputs."""

from datetime import date, datetime
from decimal import Decimal

from pyspark.sql import Row

from rideops.generators.bookings import BOOKING_FIELDS
from rideops.spark.gold import daily_city_summary
from rideops.spark.silver import (
    SPEC_BY_NAME,
    References,
    latest_versions,
    split_valid_rejected,
    transform_bookings,
    transform_customers,
)

META = {"_source_file": "f.csv", "_ingested_at": datetime(2026, 11, 1)}


def _refs(spark, customers=("CUS-000001",), drivers=("DRV-000001",)) -> References:
    return References(
        cities=spark.createDataFrame([("BLR",), ("HYD",)], "city_code string"),
        vehicle_types=spark.createDataFrame([("mini",)], "vehicle_type string"),
        customers=spark.createDataFrame([(c,) for c in customers], "customer_id string"),
        drivers=spark.createDataFrame([(d,) for d in drivers], "driver_id string"),
    )


def _booking(**overrides) -> dict:
    row = dict.fromkeys(BOOKING_FIELDS, "")
    row.update(
        booking_id="BKG-0000001", customer_id="CUS-000001", driver_id="DRV-000001",
        city="BLR", vehicle_type="mini", status="completed",
        requested_at="2026-10-01T10:00:00Z", pickup_at="2026-10-01T10:10:00Z",
        dropoff_at="2026-10-01T10:30:00Z", distance_km="5.5", surge_multiplier="1.0",
        fare_amount="150.00", currency="INR", payment_method="upi",
        updated_at="2026-10-01T10:30:00Z",
    )  # fmt: skip
    row.update(overrides)
    return {**row, **META}


def _bookings_df(spark, *rows):
    return spark.createDataFrame([Row(**r) for r in rows])


def test_valid_booking_passes_and_gets_ist_business_date(spark) -> None:
    out = transform_bookings(_bookings_df(spark, _booking()), _refs(spark)).first()
    assert out["_reject_reason"] is None
    assert out["fare_amount"] == Decimal("150.00")
    assert out["booking_date"] == date(2026, 10, 1)
    assert out["dq_flags"] == []


def test_late_utc_evening_belongs_to_next_ist_day(spark) -> None:
    row = _booking(requested_at="2026-10-01T20:00:00Z")  # 01:30 IST on 2 October
    assert transform_bookings(_bookings_df(spark, row), _refs(spark)).first()["booking_date"] == (
        date(2026, 10, 2)
    )


def test_rejection_reasons_follow_the_sql_rules(spark) -> None:
    rows = [
        _booking(booking_id="BKG-0000001", customer_id=""),
        _booking(booking_id="BKG-0000002", customer_id="CUS-999999"),
        _booking(booking_id="BKG-0000003", fare_amount="-10"),
        _booking(booking_id="BKG-0000004", pickup_at="2026-10-01T10:40:00Z"),
        _booking(booking_id="BAD", status="completed"),
    ]
    out = {
        r["booking_id"]: r["_reject_reason"]
        for r in transform_bookings(_bookings_df(spark, *rows), _refs(spark)).collect()
    }
    assert out == {
        "BKG-0000001": "missing customer_id",
        "BKG-0000002": "unknown customer_id",
        "BKG-0000003": "invalid fare_amount",
        "BKG-0000004": "dropoff before pickup",
        "BAD": "invalid booking_id",
    }


def test_repairable_problems_are_flagged_not_rejected(spark) -> None:
    row = _booking(status=" COMPLETED ", requested_at="01/10/2026 10:00", driver_id="DRV-000777",
                   payment_method="")  # fmt: skip
    out = transform_bookings(_bookings_df(spark, row), _refs(spark)).first()
    assert out["_reject_reason"] is None
    assert out["status"] == "completed"
    assert out["requested_at"] == datetime(2026, 10, 1, 10, 0)
    assert set(out["dq_flags"]) == {
        "status_normalised", "legacy_timestamp_standardised", "unknown_driver_id",
        "missing_payment_method",
    }  # fmt: skip


def test_customer_bad_email_is_nulled_and_flagged(spark) -> None:
    df = spark.createDataFrame([Row(
        customer_id="CUS-000001", full_name="A B", email="a_at_example.com", phone="+91",
        city="XXX", loyalty_tier="gold", signup_ts="2026-01-01T00:00:00Z",
        updated_at="2026-01-02T00:00:00Z", **META,
    )])  # fmt: skip
    out = transform_customers(df, _refs(spark)).first()
    assert out["_reject_reason"] is None
    assert out["email"] is None and out["city"] is None
    assert set(out["dq_flags"]) == {"malformed_email", "unknown_city"}


def test_latest_version_wins_and_split(spark) -> None:
    rows = [
        _booking(updated_at="2026-10-01T10:30:00Z", fare_amount="100"),
        _booking(updated_at="2026-10-02T09:00:00Z", fare_amount="120"),  # later update
        _booking(booking_id="BKG-0000009", fare_amount="-1"),
    ]
    staged = transform_bookings(_bookings_df(spark, *rows), _refs(spark))
    assert latest_versions(staged, "booking_id").count() == 2
    valid, rejected = split_valid_rejected(staged, SPEC_BY_NAME["bookings"])
    assert [r["fare_amount"] for r in valid.collect()] == [Decimal("120.00")]
    assert [(r["record_key"], r["reason"]) for r in rejected.collect()] == [
        ("BKG-0000009", "invalid fare_amount")
    ]


def test_daily_city_summary_counts_and_revenue(spark) -> None:
    rows = [
        _booking(booking_id="BKG-0000001", fare_amount="100"),
        _booking(
            booking_id="BKG-0000002",
            status="cancelled",
            fare_amount="50",
            pickup_at="",
            dropoff_at="",
            cancelled_by="customer",
        ),  # fmt: skip
        _booking(
            booking_id="BKG-0000003",
            status="no_driver_found",
            fare_amount="0",
            pickup_at="",
            dropoff_at="",
            driver_id="",
        ),  # fmt: skip
    ]
    silver = transform_bookings(_bookings_df(spark, *rows), _refs(spark))
    out = daily_city_summary(silver).first()
    assert (out["total_bookings"], out["completed_rides"], out["cancelled_bookings"],
            out["no_driver_bookings"]) == (3, 1, 1, 1)  # fmt: skip
    assert out["gross_revenue"] == Decimal("150.00")
    assert out["cancellation_rate"] == round(1 / 3, 4)
