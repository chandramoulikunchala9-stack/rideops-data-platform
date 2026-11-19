"""End-to-end lakehouse tests on a small generated dataset in a temporary directory."""

import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from delta.tables import DeltaTable
from pyspark.sql import functions as F

from rideops.generators.__main__ import main as generate
from rideops.spark import bronze, gold, silver, streaming
from rideops.spark.delta_demo import run_demo
from rideops.spark.lakehouse import Lakehouse
from rideops.spark.quality import run_expectations

ENTITIES = ("vehicles", "drivers", "customers", "bookings", "payments")


@pytest.fixture(scope="module")
def lake(tmp_path_factory) -> Lakehouse:
    data_dir = tmp_path_factory.mktemp("lake")
    assert generate(["--scale", "small", "--seed", "7", "--data-dir", str(data_dir)]) == 0
    return Lakehouse(data_dir)


@pytest.fixture(scope="module")
def built(spark, lake) -> Lakehouse:
    for entity in ENTITIES:
        bronze.ingest(spark, lake, entity)
    silver.process_all(spark, lake)
    gold.build_gold(spark, lake)
    return lake


def _read(spark, path):
    return spark.read.format("delta").load(path)


def test_bronze_matches_manifest_and_is_exactly_once(spark, built) -> None:
    manifest = json.loads(
        (built.data_dir / "landing" / "_manifests" / "generation_0001.json").read_text("utf-8")
    )
    for entity in ENTITIES:
        assert _read(spark, built.bronze(entity)).count() == manifest["entities"][entity]["rows"]
    assert bronze.ingest(spark, built, "bookings") == 0  # checkpoint: nothing new to ingest


def test_every_booking_is_in_silver_or_quarantine(spark, built) -> None:
    distinct = _read(spark, built.bronze("bookings")).select("booking_id").distinct().count()
    in_silver = _read(spark, built.silver("bookings")).count()
    quarantined = (
        _read(spark, built.quarantine).filter(F.col("entity") == "bookings")
        .select("record_key").distinct().count()
    )  # fmt: skip
    assert in_silver + quarantined == distinct


def test_silver_keys_are_unique_and_rules_hold(spark, built) -> None:
    results = {r.check_name: r for r in run_expectations(spark, built)}
    for name in ("lakehouse_bookings_key_unique", "lakehouse_bookings_fare_non_negative",
                 "lakehouse_payments_currency_inr", "lakehouse_drivers_rating_in_range"):  # fmt: skip
        assert results[name].status == "PASS", name


def test_gold_reconciles_with_silver(spark, built) -> None:
    silver_bookings = _read(spark, built.silver("bookings"))
    summary = _read(spark, built.gold("daily_city_summary"))
    totals = summary.agg(F.sum("total_bookings"), F.sum("gross_revenue")).first()
    assert totals[0] == silver_bookings.count()
    assert totals[1] == silver_bookings.agg(F.sum("fare_amount")).first()[0]


def test_silver_rerun_is_incremental_and_gold_sees_no_changes(spark, built) -> None:
    assert silver.process_entity(spark, built, silver.SPEC_BY_NAME["bookings"]) == {
        "read": 0, "written": 0, "rejected": 0
    }  # fmt: skip
    dates, _ = gold.changed_booking_dates(spark, built)
    assert dates == []


def test_gold_recomputes_only_dates_touched_by_a_new_file(spark, built) -> None:
    # Re-deliver one booking with a later updated_at and a new fare, in a new landing file.
    original = _read(spark, built.silver("bookings")).filter("status = 'completed'").first()
    landing = Path(built.landing("bookings")) / "batch_date=2026-11-01"
    landing.mkdir(parents=True, exist_ok=True)
    src = next(Path(built.landing("bookings")).glob("batch_date=2026-10-31/*.csv"))
    with src.open(encoding="utf-8", newline="") as fh:
        row = next(r for r in csv.DictReader(fh) if r["booking_id"] == original["booking_id"])
    row["fare_amount"] = "9999.00"
    row["updated_at"] = (original["updated_at"] + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with (landing / "bookings_20261101_0002.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=bronze.SOURCES["bookings"][1])
        writer.writeheader()
        writer.writerow(row)

    assert bronze.ingest(spark, built, "bookings") == 1
    silver.process_entity(spark, built, silver.SPEC_BY_NAME["bookings"])
    dates, _ = gold.changed_booking_dates(spark, built)
    assert dates == [original["booking_date"]]

    gold.build_gold(spark, built)
    day = _read(spark, built.gold("daily_city_summary")).filter(
        (F.col("booking_date") == original["booking_date"]) & (F.col("city") == original["city"])
    )
    expected = (
        _read(spark, built.silver("bookings"))
        .filter((F.col("booking_date") == original["booking_date"]) & (F.col("city") == original["city"]))
        .agg(F.sum("fare_amount")).first()[0]
    )  # fmt: skip
    assert day.first()["gross_revenue"] == expected
    history = [
        r["operation"]
        for r in DeltaTable.forPath(spark, built.gold("daily_city_summary")).history().collect()
    ]
    assert "WRITE" in history  # replaceWhere overwrite of one date is a WRITE commit


def test_streaming_watermark_drops_very_late_events(spark, tmp_path) -> None:
    lake = Lakehouse(tmp_path)
    base = datetime(2026, 10, 1, 10, 0)

    def event(i: int, minutes: float) -> dict:
        ts = (base + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"event_id": f"e{i}", "event_type": "ride_requested", "booking_id": f"b{i}",
                "customer_id": "c", "driver_id": None, "city": "BLR", "latitude": "1",
                "longitude": "1", "event_ts": ts, "ingest_ts": ts}  # fmt: skip

    # With two stateful operators (dedup + window aggregation) Spark drops an event as late
    # only if it is older than the watermark at the end of the *previous* batch, so the very
    # late event arrives one delivery after the watermark has moved past it.
    batches = [
        [event(1, 0), event(2, 5), event(3, 20)],  # watermark after batch: 09:50
        [event(4, 120), event(1, 0)],  # e1 re-delivered (duplicate); watermark -> 11:30
        [event(7, 150)],  # watermark -> 12:00
        [event(5, 10), event(6, 140)],  # e5 is ~2h late -> dropped; e6 within 30 min
    ]
    for day, rows in enumerate(batches, start=1):
        folder = Path(lake.landing("ride_events")) / f"batch_date=2026-10-0{day}"
        folder.mkdir(parents=True)
        file = folder / f"ride_events_2026100{day}_0001.jsonl"
        file.write_text("\n".join(json.dumps(r) for r in rows) + "\n", "utf-8")
        # The file source orders files by modification time; files written within the same
        # millisecond have no defined order, so give each delivery a later mtime.
        os.utime(file, (1_700_000_000 + day * 60, 1_700_000_000 + day * 60))

    counts = streaming.run(spark, lake, files_per_trigger=1)
    assert counts["read"] == 8
    assert counts["rejected"] >= 1  # e5 dropped by the watermark
    windows = {
        (r["window_start"].strftime("%H:%M"), r["events"])
        for r in _read(spark, lake.gold("ride_event_windows")).collect()
    }
    # The 10:00 window is final (watermark passed 10:15) and counts e1 once, e2 once.
    assert ("10:00", 2) in windows


def test_delta_feature_tour(spark, tmp_path) -> None:
    facts = run_demo(spark, str(tmp_path / "demo"))
    assert facts["rows_after_failed_write"] == 6  # ACID: no partial append
    assert facts["schema_enforced"] is True
    assert "method" in facts["columns_after_evolution"]
    assert facts["null_methods_for_old_rows"] == 6
    assert facts["fare_of_id_1"] == 999.0 and facts["rows_after_merge"] == 8
    assert facts["rows_at_version_0"] == 6 and facts["fare_of_id_1_at_v0"] == 10.0
    # History is captured before OPTIMIZE. The failed append committed nothing, so exactly:
    assert facts["operations"] == ["WRITE", "WRITE", "MERGE"]
    assert facts["files_after_optimize"] < facts["files_before_optimize"]
    assert facts["time_travel_after_vacuum"] is False
