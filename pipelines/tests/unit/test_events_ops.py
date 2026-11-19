from collections import Counter
from datetime import date, timedelta

import pytest

from rideops.generators.bookings import build_bookings
from rideops.generators.common import parse_ts
from rideops.generators.defects import NO_DEFECTS, DefectRates
from rideops.generators.entities import generate_customers, generate_drivers, generate_vehicles
from rideops.generators.events import (
    RIDE_EVENT_TYPES,
    build_app_events,
    build_ride_events,
    generate_ride_events,
)
from rideops.generators.ops import (
    INCIDENT_DAYS_BEFORE_END,
    INCIDENT_PIPELINE,
    RUNAWAY_DAYS_BEFORE_END,
    build_cloud_costs,
    build_pipeline_runs,
    generate_ops,
)
from rideops.generators.reference import PIPELINES

END = date(2026, 10, 31)
DAYS = 35
COMMON = {"end_date": END, "history_days": DAYS}


@pytest.fixture(scope="module")
def bookings():
    vehicles = generate_vehicles(100, seed=5, defects=NO_DEFECTS, **COMMON)
    drivers = generate_drivers(200, seed=5, vehicles=vehicles.records, defects=NO_DEFECTS, **COMMON)
    customers = generate_customers(800, seed=5, defects=NO_DEFECTS, **COMMON)
    return build_bookings(
        3_000,
        seed=5,
        customers=customers.records,
        drivers=drivers.records,
        vehicles=vehicles.records,
        **COMMON,
    )


@pytest.fixture(scope="module")
def runs(bookings):
    return build_pipeline_runs(bookings, seed=5, **COMMON)


# ------------------------------------------------------------------ ride events


def test_ride_events_follow_each_booking_life_cycle(bookings) -> None:
    events = build_ride_events(bookings, seed=5)
    by_booking: dict[str, list[str]] = {}
    for e in events:
        by_booking.setdefault(e["booking_id"], []).append(e["event_type"])

    assert set(by_booking) == {b["booking_id"] for b in bookings}
    assert {e["event_type"] for e in events} <= set(RIDE_EVENT_TYPES)
    for b in bookings:
        types = by_booking[b["booking_id"]]
        assert types[0] == "ride_requested"
        if b["status"] == "completed":
            assert types == ["ride_requested", "driver_assigned", "ride_started", "ride_completed"]
        elif b["status"] == "no_driver_found":
            assert types == ["ride_requested", "no_driver_found"]
        elif b["status"] == "cancelled":
            assert types[-1] == "ride_cancelled"


def test_events_arrive_after_they_happen_and_some_arrive_late(bookings) -> None:
    events = build_ride_events(bookings, seed=5)
    delays = [parse_ts(e["ingest_ts"]) - parse_ts(e["event_ts"]) for e in events]
    assert all(d >= timedelta(0) for d in delays)
    late = sum(d > timedelta(minutes=5) for d in delays) / len(delays)
    assert 0.015 < late < 0.05  # ~3% late-arriving


def test_event_ids_are_unique_and_reproducible(bookings) -> None:
    a = build_ride_events(bookings, seed=5)
    b = build_ride_events(bookings, seed=5)
    assert a == b
    assert len({e["event_id"] for e in a}) == len(a)


def test_ride_event_batch_is_partitioned_by_ingest_time(bookings) -> None:
    batch = generate_ride_events(bookings, seed=5, defects=DefectRates())
    assert batch.partition_by == "ingest_ts"
    null_island = [e for e in batch.records if (e["latitude"], e["longitude"]) == (0.0, 0.0)]
    assert null_island  # GPS-failure defect is present


def test_app_sessions_end_in_booking_or_are_abandoned(bookings) -> None:
    events = build_app_events(bookings, seed=5)
    sessions: dict[str, list[str]] = {}
    for e in events:
        sessions.setdefault(e["session_id"], []).append(e["event_type"])
    with_booking = [s for s in sessions.values() if "booking_requested" in s]
    abandoned = [s for s in sessions.values() if "booking_requested" not in s]
    assert len(with_booking) == len(bookings)
    assert 0.15 * len(bookings) < len(abandoned) < 0.35 * len(bookings)
    assert all(s[0] == "app_opened" for s in sessions.values())


# ------------------------------------------------------------------ pipeline runs


def test_every_pipeline_runs_every_day(runs) -> None:
    first_attempts = [r for r in runs if r["retry_count"] == 0]
    assert len(first_attempts) == len(PIPELINES) * DAYS
    assert {r["run_source"] for r in runs} == {"simulated_history"}


def test_status_values_and_failure_details(runs) -> None:
    statuses = Counter(r["status"] for r in runs)
    assert set(statuses) <= {"SUCCESS", "FAILED", "CANCELLED", "RUNNING"}
    assert statuses["RUNNING"] == 1
    for r in runs:
        if r["status"] in ("FAILED", "CANCELLED"):
            assert r["error_message"]
        if r["status"] == "SUCCESS":
            assert r["error_message"] is None
            assert r["records_written"] + r["records_rejected"] == r["records_read"]
            assert parse_ts(r["end_ts"]) >= parse_ts(r["start_ts"])


def test_incident_fails_all_retries_and_cancels_downstream(runs) -> None:
    day = (END - timedelta(days=INCIDENT_DAYS_BEFORE_END)).strftime("%Y%m%d")
    incident = [r for r in runs if r["run_id"].startswith(f"RUN-{day}-{INCIDENT_PIPELINE}")]
    assert [r["status"] for r in incident] == ["FAILED", "FAILED", "FAILED"]
    assert all("DELTA_FAILED_TO_MERGE_FIELDS" in r["error_message"] for r in incident)

    cancelled = {
        r["pipeline_name"]
        for r in runs
        if r["run_id"].startswith(f"RUN-{day}-") and r["status"] == "CANCELLED"
    }
    assert {"silver_transform", "gold_aggregations", "export_to_postgres"} <= cancelled


def test_downstream_pipelines_start_after_upstream_finishes(runs) -> None:
    finish: dict[tuple[str, str], object] = {}
    for r in runs:
        if r["status"] == "SUCCESS":
            finish[(r["run_id"][4:12], r["pipeline_name"])] = parse_ts(r["end_ts"])
    for spec in PIPELINES:
        for r in runs:
            if r["pipeline_name"] != spec.name or r["status"] != "SUCCESS":
                continue
            for dep in spec.depends_on:
                upstream_end = finish.get((r["run_id"][4:12], dep))
                assert upstream_end is None or parse_ts(r["start_ts"]) >= upstream_end


# ------------------------------------------------------------------ cloud costs


def test_costs_are_labelled_simulated_and_reconcile(runs) -> None:
    costs = build_cloud_costs(runs, seed=5, **COMMON)
    assert {c["source"] for c in costs} == {"simulated"}
    assert {c["currency"] for c in costs} == {"USD"}
    for c in costs:
        assert c["cost_usd"] == pytest.approx(c["usage_quantity"] * c["unit_price_usd"], abs=1e-3)


def test_runaway_cluster_creates_a_cost_anomaly(runs) -> None:
    costs = build_cloud_costs(runs, seed=5, **COMMON)
    daily: Counter = Counter()
    for c in costs:
        daily[c["usage_date"]] += c["cost_usd"]
    median = sorted(daily.values())[len(daily) // 2]
    for days_before in RUNAWAY_DAYS_BEFORE_END:
        assert daily[(END - timedelta(days=days_before)).isoformat()] > 3 * median


def test_untagged_costs_are_injected_as_missing_workload(bookings) -> None:
    _, costs = generate_ops(bookings, defects=DefectRates(missing=0.05), seed=5, **COMMON)
    untagged = [c for c in costs.records if c["workload"] is None]
    assert len(untagged) >= costs.defects["missing"] > 0
