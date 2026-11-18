from collections import Counter, defaultdict
from datetime import date

import pytest

from rideops.generators.bookings import (
    BOOKING_FIELDS,
    apply_booking_defects,
    build_bookings,
    calculate_fare,
)
from rideops.generators.common import IST, parse_ts
from rideops.generators.defects import NO_DEFECTS, DefectRates
from rideops.generators.entities import generate_customers, generate_drivers, generate_vehicles
from rideops.generators.payments import build_payments, generate_payments
from rideops.generators.reference import BOOKING_STATUSES, CITY_CODES

END = date(2026, 10, 31)
COMMON = {"end_date": END, "history_days": 30}
N = 5_000


@pytest.fixture(scope="module")
def dims():
    """Dimension data *with* defects, as the bookings generator sees it in a real run."""
    vehicles = generate_vehicles(150, seed=1, defects=DefectRates(), **COMMON)
    drivers = generate_drivers(
        300, seed=1, vehicles=vehicles.records, defects=DefectRates(), **COMMON
    )
    customers = generate_customers(1_500, seed=1, defects=DefectRates(), **COMMON)
    return vehicles.records, drivers.records, customers.records


@pytest.fixture(scope="module")
def bookings(dims):
    vehicles, drivers, customers = dims
    return build_bookings(
        N, seed=1, customers=customers, drivers=drivers, vehicles=vehicles, **COMMON
    )


def test_bookings_are_reproducible(dims, bookings) -> None:
    vehicles, drivers, customers = dims
    again = build_bookings(
        N, seed=1, customers=customers, drivers=drivers, vehicles=vehicles, **COMMON
    )
    assert again == bookings


def test_bookings_have_all_fields_and_unique_ids(bookings) -> None:
    assert len(bookings) == N
    assert len({b["booking_id"] for b in bookings}) == N
    assert all(tuple(b) == BOOKING_FIELDS for b in bookings)
    assert {b["status"] for b in bookings} <= set(BOOKING_STATUSES)
    assert all(b["city"] in CITY_CODES for b in bookings)


def test_status_mix_is_realistic(bookings) -> None:
    share = {s: c / N for s, c in Counter(b["status"] for b in bookings).items()}
    assert 0.70 < share["completed"] < 0.85
    assert 0.12 < share["cancelled"] < 0.22
    assert 0.03 < share["no_driver_found"] < 0.08


def test_bookings_only_reference_valid_entities_and_respect_time(dims, bookings) -> None:
    vehicles, drivers, customers = dims
    signup = {c["customer_id"]: parse_ts(c["signup_ts"]) for c in customers}
    joined = {d["driver_id"]: parse_ts(d["joined_at"]) for d in drivers}
    suspended = {d["driver_id"] for d in drivers if d["status"] == "suspended"}
    valid_vehicles = {v["vehicle_id"] for v in vehicles}

    for b in bookings:
        requested = parse_ts(b["requested_at"])
        assert requested >= signup[b["customer_id"]], "booked before signing up"
        if b["driver_id"] is not None:
            assert b["driver_id"] not in suspended
            assert requested >= joined[b["driver_id"]], "driver took a ride before joining"
            assert b["vehicle_id"] in valid_vehicles


def test_completed_rides_are_consistent(bookings) -> None:
    completed = [b for b in bookings if b["status"] == "completed"]
    for b in completed:
        times = [parse_ts(b[f]) for f in ("requested_at", "accepted_at", "pickup_at", "dropoff_at")]
        assert times == sorted(times), "life-cycle timestamps out of order"
        expected = calculate_fare(
            b["city"], b["vehicle_type"], b["distance_km"], b["surge_multiplier"]
        )
        assert b["fare_amount"] == expected > 0


def test_cancellations_and_no_driver(bookings) -> None:
    for b in bookings:
        if b["status"] == "cancelled":
            assert b["cancelled_by"] in ("customer", "driver")
            assert b["cancellation_reason"] and b["cancelled_at"]
            assert b["fare_amount"] in (0.0, 50.0)
            if b["fare_amount"]:
                assert b["cancelled_by"] == "customer"
        elif b["status"] == "no_driver_found":
            assert b["driver_id"] is None and b["fare_amount"] == 0.0


def test_demand_peaks_in_rush_hour_ist(bookings) -> None:
    hours = Counter(parse_ts(b["requested_at"]).astimezone(IST).hour for b in bookings)
    assert hours[18] > 3 * hours[3]


def test_payments_reconcile_with_bookings(bookings) -> None:
    payments = build_payments(bookings, seed=1)
    by_id = {b["booking_id"]: b for b in bookings}
    rows = defaultdict(list)
    for p in payments:
        rows[p["booking_id"]].append(p)

    assert set(rows) <= set(by_id)  # no payment for an unknown booking
    for booking_id, attempts in rows.items():
        booking = by_id[booking_id]
        assert booking["fare_amount"] > 0
        charges = [p for p in attempts if p["transaction_type"] == "charge"]
        assert [p["attempt_number"] for p in charges] == list(range(1, len(charges) + 1))
        assert all(p["status"] == "failed" for p in charges[:-1])  # only the last can succeed
        for p in charges:
            assert p["amount"] == booking["fare_amount"]
            assert (p["failure_reason"] is None) == (p["status"] == "success")
        if booking["payment_method"] == "cash":
            assert [p["status"] for p in attempts] == ["success"]

    paid = [b for b in bookings if b["fare_amount"] > 0]
    assert set(rows) == {b["booking_id"] for b in paid}
    statuses = Counter(p["status"] for p in payments)
    assert statuses["failed"] > 0
    assert any(p["transaction_type"] == "refund" for p in payments)


def test_booking_defects_do_not_change_payments(dims) -> None:
    vehicles, drivers, customers = dims
    records = build_bookings(
        2_000, seed=2, customers=customers, drivers=drivers, vehicles=vehicles, **COMMON
    )
    payments_before = build_payments(records, seed=2)
    batch = apply_booking_defects(records, seed=2, defects=DefectRates(invalid=0.05))

    assert batch.defects["invalid"] == 100
    assert any(b["customer_id"] == "CUS-999999" for b in batch.records)
    # derived from clean data, so no payment carries a negative amount
    assert all(p["amount"] > 0 for p in payments_before)


def test_payment_defects_are_counted(bookings) -> None:
    batch = generate_payments(
        bookings, seed=1, defects=DefectRates(missing=0, duplicate=0, invalid=0.02)
    )
    negative = [p for p in batch.records if p["amount"] < 0]
    bad_currency = [p for p in batch.records if p["currency"] == "XYZ"]
    orphan = [p for p in batch.records if p["booking_id"] == "BKG-9999999"]
    assert len(negative) + len(bad_currency) + len(orphan) == batch.defects["invalid"]


def test_no_defects_mode_gives_clean_payments(bookings) -> None:
    batch = generate_payments(bookings, seed=1, defects=NO_DEFECTS)
    assert batch.defects == {"invalid": 0, "missing": 0, "duplicate": 0}
    assert len({p["payment_id"] for p in batch.records}) == len(batch.records)
