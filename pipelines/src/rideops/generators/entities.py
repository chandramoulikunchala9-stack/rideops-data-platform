"""Generators for the slowly-changing 'dimension' entities: vehicles, drivers, customers.

Every generator follows the same three steps:
  1. build clean, valid records;
  2. inject defects (missing, invalid, duplicate) at the configured rates;
  3. return an EntityBatch with the records and the exact defect counts.
"""

import random
import re
from datetime import date, timedelta

from rideops.generators.common import (
    EntityBatch,
    bump_updated_at,
    end_of_day_utc,
    make_faker,
    make_rng,
    pick_city,
    random_datetime,
    to_iso,
    to_legacy_format,
)
from rideops.generators.defects import DefectRates, Record, apply_defects
from rideops.generators.reference import (
    DRIVER_STATUS_WEIGHTS,
    DRIVER_STATUSES,
    EMAIL_DOMAINS,
    LOYALTY_TIERS,
    LOYALTY_WEIGHTS,
    VEHICLE_TYPES,
)

VEHICLE_FIELDS = (
    "vehicle_id",
    "registration_number",
    "vehicle_type",
    "make",
    "model",
    "manufacture_year",
    "seats",
    "city",
    "is_active",
    "updated_at",
)
DRIVER_FIELDS = (
    "driver_id",
    "full_name",
    "phone",
    "city",
    "license_number",
    "vehicle_id",
    "rating",
    "status",
    "joined_at",
    "updated_at",
)
CUSTOMER_FIELDS = (
    "customer_id",
    "full_name",
    "email",
    "phone",
    "city",
    "loyalty_tier",
    "signup_ts",
    "updated_at",
)


def _indian_mobile(rng: random.Random) -> str:
    return f"+91{rng.randint(6_000_000_000, 9_999_999_999)}"


# --------------------------------------------------------------------------- vehicles


def _corrupt_vehicle(record: Record, rng: random.Random) -> None:
    choice = rng.randrange(3)
    if choice == 0:
        record["manufacture_year"] = rng.choice([1890, 2099])  # impossible year
    elif choice == 1:
        record["seats"] = 0
    else:
        record["vehicle_type"] = "spaceship"  # not in the allowed list


def generate_vehicles(
    n: int, *, seed: int, end_date: date, history_days: int, defects: DefectRates
) -> EntityBatch:
    rng = make_rng(seed, "vehicles")
    end = end_of_day_utc(end_date)
    start = end - timedelta(days=history_days + 365)
    type_weights = [t.weight for t in VEHICLE_TYPES]

    records: list[Record] = []
    used_registrations: set[str] = set()
    for i in range(1, n + 1):
        city = pick_city(rng)
        vtype = rng.choices(VEHICLE_TYPES, weights=type_weights)[0]
        make, model = rng.choice(vtype.models)
        while True:  # registration numbers must be unique, retry on the rare collision
            letters = "".join(rng.choices("ABCDEFGHJKLMNPRSTUVWXYZ", k=2))
            reg = f"{city.state_code}{rng.randint(1, 40):02d}{letters}{rng.randint(1, 9999):04d}"
            if reg not in used_registrations:
                used_registrations.add(reg)
                break
        records.append(
            {
                "vehicle_id": f"VEH-{i:06d}",
                "registration_number": reg,
                "vehicle_type": vtype.name,
                "make": make,
                "model": model,
                "manufacture_year": rng.randint(end_date.year - 10, end_date.year),
                "seats": vtype.seats,
                "city": city.code,
                "is_active": rng.random() < 0.95,
                "updated_at": to_iso(random_datetime(rng, start, end)),
            }
        )

    counts = apply_defects(
        records,
        rng,
        defects,
        corrupt=_corrupt_vehicle,
        nullable_fields=("make", "model", "manufacture_year"),
        mutate_duplicate=bump_updated_at,
    )
    return EntityBatch("vehicles", records, VEHICLE_FIELDS, "jsonl", counts)


# --------------------------------------------------------------------------- drivers


def _corrupt_driver(record: Record, rng: random.Random) -> None:
    choice = rng.randrange(3)
    if choice == 0:
        record["rating"] = rng.choice([-1.0, 6.3])  # ratings must be within 1.0-5.0
    elif choice == 1:
        record["vehicle_id"] = "VEH-999999"  # orphan foreign key: vehicle does not exist
    else:
        record["license_number"] = ""  # blank string, not NULL: a different kind of bad


def generate_drivers(
    n: int,
    *,
    seed: int,
    end_date: date,
    history_days: int,
    vehicles: list[Record],
    defects: DefectRates,
) -> EntityBatch:
    """Drivers are assigned a vehicle from their own city.

    There are fewer vehicles than drivers: like a real fleet, vehicles are shared across shifts.
    """
    rng = make_rng(seed, "drivers")
    fake = make_faker(seed, "drivers")
    end = end_of_day_utc(end_date)
    start = end - timedelta(days=history_days + 365)

    # Group valid vehicle IDs by city. Duplicated rows would bias the choice, so de-duplicate.
    ids_by_city: dict[str, set[str]] = {}
    for v in vehicles:
        ids_by_city.setdefault(v["city"], set()).add(v["vehicle_id"])
    # sorted() turns each set into a list in a stable order, so rng.choice stays reproducible.
    vehicles_by_city = {city: sorted(ids) for city, ids in ids_by_city.items()}
    all_vehicle_ids = sorted({v["vehicle_id"] for v in vehicles})

    records: list[Record] = []
    for i in range(1, n + 1):
        city = pick_city(rng)
        joined = random_datetime(rng, start, end)
        pool = vehicles_by_city.get(city.code) or all_vehicle_ids
        records.append(
            {
                "driver_id": f"DRV-{i:06d}",
                "full_name": fake.name(),
                "phone": _indian_mobile(rng),
                "city": city.code,
                "license_number": f"{city.state_code}{rng.randint(1, 40):02d}"
                f"{rng.randint(2005, end_date.year)}{rng.randint(0, 9_999_999):07d}",
                "vehicle_id": rng.choice(pool),
                "rating": round(rng.triangular(3.5, 5.0, 4.6), 2),
                "status": rng.choices(DRIVER_STATUSES, weights=DRIVER_STATUS_WEIGHTS)[0],
                "joined_at": to_iso(joined),
                "updated_at": to_iso(random_datetime(rng, joined, end)),
            }
        )

    counts = apply_defects(
        records,
        rng,
        defects,
        corrupt=_corrupt_driver,
        nullable_fields=("phone", "vehicle_id", "rating"),
        mutate_duplicate=bump_updated_at,
    )
    return EntityBatch("drivers", records, DRIVER_FIELDS, "csv", counts)


# --------------------------------------------------------------------------- customers


def _corrupt_customer(record: Record, rng: random.Random) -> None:
    choice = rng.randrange(3)
    if choice == 0:
        record["email"] = record["email"].replace("@", "_at_")  # malformed email
    elif choice == 1:
        record["signup_ts"] = to_legacy_format(record["signup_ts"])  # non-ISO timestamp
    else:
        record["city"] = "XXX"  # unknown city code


def generate_customers(
    n: int, *, seed: int, end_date: date, history_days: int, defects: DefectRates
) -> EntityBatch:
    rng = make_rng(seed, "customers")
    fake = make_faker(seed, "customers")
    end = end_of_day_utc(end_date)
    start = end - timedelta(days=history_days + 730)  # customers signed up over ~2 years

    records: list[Record] = []
    for i in range(1, n + 1):
        first, last = fake.first_name(), fake.last_name()
        signup = random_datetime(rng, start, end)
        local_part = re.sub(r"[^a-z]", "", f"{first}.{last}".lower()) + str(i)
        records.append(
            {
                "customer_id": f"CUS-{i:06d}",
                "full_name": f"{first} {last}",
                "email": f"{local_part}@{rng.choice(EMAIL_DOMAINS)}",
                "phone": _indian_mobile(rng),
                "city": pick_city(rng).code,
                "loyalty_tier": rng.choices(LOYALTY_TIERS, weights=LOYALTY_WEIGHTS)[0],
                "signup_ts": to_iso(signup),
                "updated_at": to_iso(random_datetime(rng, signup, end)),
            }
        )

    counts = apply_defects(
        records,
        rng,
        defects,
        corrupt=_corrupt_customer,
        nullable_fields=("email", "phone", "loyalty_tier"),
        mutate_duplicate=bump_updated_at,
    )
    return EntityBatch("customers", records, CUSTOMER_FIELDS, "csv", counts)
