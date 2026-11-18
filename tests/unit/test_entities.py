import re
from datetime import date

import pytest

from rideops.generators.defects import NO_DEFECTS, DefectRates
from rideops.generators.entities import generate_customers, generate_drivers, generate_vehicles
from rideops.generators.reference import CITY_CODES, EMAIL_DOMAINS

END = date(2026, 10, 31)
COMMON = {"end_date": END, "history_days": 30}
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.fixture(scope="module")
def clean_vehicles():
    return generate_vehicles(300, seed=1, defects=NO_DEFECTS, **COMMON)


# ------------------------------------------------------------------ reproducibility


def test_same_seed_produces_identical_data() -> None:
    a = generate_customers(200, seed=42, defects=DefectRates(), **COMMON)
    b = generate_customers(200, seed=42, defects=DefectRates(), **COMMON)
    assert a.records == b.records


def test_different_seed_produces_different_data() -> None:
    a = generate_customers(200, seed=42, defects=NO_DEFECTS, **COMMON)
    b = generate_customers(200, seed=43, defects=NO_DEFECTS, **COMMON)
    assert a.records != b.records


# ------------------------------------------------------------------ clean data is valid


def test_clean_customers_are_valid() -> None:
    batch = generate_customers(500, seed=1, defects=NO_DEFECTS, **COMMON)
    assert len(batch.records) == 500
    assert len({r["customer_id"] for r in batch.records}) == 500
    for r in batch.records:
        assert set(r) == set(batch.fieldnames)
        assert r["city"] in CITY_CODES
        assert r["email"].split("@")[1] in EMAIL_DOMAINS  # never a real mail domain
        assert ISO_UTC.match(r["signup_ts"])
        assert r["signup_ts"] <= r["updated_at"]  # ISO strings sort chronologically


def test_clean_vehicles_are_valid(clean_vehicles) -> None:
    regs = [v["registration_number"] for v in clean_vehicles.records]
    assert len(set(regs)) == len(regs)  # registration numbers are unique
    for v in clean_vehicles.records:
        assert END.year - 10 <= v["manufacture_year"] <= END.year
        assert v["seats"] >= 1


def test_drivers_reference_existing_vehicles_in_their_city(clean_vehicles) -> None:
    vehicle_city = {v["vehicle_id"]: v["city"] for v in clean_vehicles.records}
    drivers = generate_drivers(
        1000, seed=1, vehicles=clean_vehicles.records, defects=NO_DEFECTS, **COMMON
    )
    for d in drivers.records:
        assert d["vehicle_id"] in vehicle_city  # referential integrity
        assert vehicle_city[d["vehicle_id"]] == d["city"]
        assert 1.0 <= d["rating"] <= 5.0


# ------------------------------------------------------------------ defects are injected


def test_defect_counts_match_what_is_in_the_data() -> None:
    rates = DefectRates(missing=0.03, duplicate=0.02, invalid=0.01)
    batch = generate_customers(1000, seed=3, defects=rates, **COMMON)

    assert batch.defects == {"invalid": 10, "missing": 30, "duplicate": 20}
    assert len(batch.records) == 1020
    rows_with_null = [r for r in batch.records if None in r.values()]
    # duplicates may copy a row that has a null, so >= rather than ==
    assert len(rows_with_null) >= 30
    assert len({r["customer_id"] for r in batch.records}) == 1000


def test_invalid_driver_values_are_the_documented_kinds(clean_vehicles) -> None:
    drivers = generate_drivers(
        2000,
        seed=8,
        vehicles=clean_vehicles.records,
        defects=DefectRates(missing=0, duplicate=0, invalid=0.05),
        **COMMON,
    )
    known = {v["vehicle_id"] for v in clean_vehicles.records}
    bad_rating = [d for d in drivers.records if not 1.0 <= d["rating"] <= 5.0]
    orphan = [d for d in drivers.records if d["vehicle_id"] not in known]
    blank_licence = [d for d in drivers.records if d["license_number"] == ""]

    assert len(bad_rating) + len(orphan) + len(blank_licence) == drivers.defects["invalid"] == 100
    assert {d["vehicle_id"] for d in orphan} <= {"VEH-999999"}
