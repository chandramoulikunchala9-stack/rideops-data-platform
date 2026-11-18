"""Bookings: the main fact table. One row per ride request.

A booking's life cycle:

    requested --> accepted by a driver --> picked up --> dropped off     status = completed
            \\--> no driver found                                         status = no_driver_found
            \\--> cancelled by customer or driver                         status = cancelled
    (a ride still running when the data window ends)                     status = in_progress

Generation is split in two steps so payments can be derived from *clean* bookings:
    build_bookings()        -> clean, internally consistent records
    apply_booking_defects() -> injects defects into those records (in place)
"""

import itertools
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from rideops.generators.common import (
    IST,
    EntityBatch,
    bump_updated_at,
    make_rng,
    parse_ts,
    pick_city,
    random_datetime,
    to_iso,
    to_legacy_format,
)
from rideops.generators.defects import DefectRates, Record, apply_defects
from rideops.generators.reference import (
    CANCEL_REASONS_CUSTOMER,
    CANCEL_REASONS_DRIVER,
    CITY_BY_CODE,
    CITY_CODES,
    HOUR_WEIGHTS_IST,
    LATE_NIGHT_HOURS_IST,
    PAYMENT_METHOD_WEIGHTS,
    PAYMENT_METHODS,
    PEAK_HOURS_IST,
    VEHICLE_TYPE_BY_NAME,
)

BOOKING_FIELDS = (
    "booking_id", "customer_id", "driver_id", "vehicle_id", "city", "vehicle_type", "status",
    "requested_at", "accepted_at", "pickup_at", "dropoff_at", "cancelled_at", "cancelled_by",
    "cancellation_reason", "distance_km", "duration_min", "surge_multiplier", "fare_amount",
    "currency", "payment_method", "updated_at",
)  # fmt: skip

# Business assumptions, named so they are easy to find and change.
P_NO_DRIVER = 0.05  # share of requests where no driver accepts
P_CANCELLED = 0.17  # share of requests that are cancelled
P_CANCEL_BY_CUSTOMER = 0.70  # the rest are cancelled by the driver
P_CANCEL_BEFORE_ACCEPT = 0.30  # customer cancels before any driver accepted
P_CANCELLATION_FEE = 0.50  # chance a late customer cancellation is charged
CANCELLATION_FEE_INR = 50.0
P_HOME_CITY = 0.85  # customers mostly ride in their home city
MAX_DRIVER_TRIES = 10  # attempts to find a driver who had already joined
DEMAND_GROWTH = 0.30  # demand grows 30% across the window
WEEKEND_UPLIFT = 1.15


@dataclass(frozen=True)
class _Driver:
    driver_id: str
    vehicle_id: str
    vehicle_type: str
    joined_at: datetime


# --------------------------------------------------------------------------- indexes
# The dimension records contain defects (duplicates, unknown cities, orphan vehicle IDs).
# Bookings must only reference *valid* entities, so we first build clean lookup indexes.


def _index_customers(customers: list[Record], window_end: datetime) -> dict[str, tuple]:
    """customer_id -> (home city or None, signup time). Skips customers who cannot book."""
    index: dict[str, tuple[str | None, datetime]] = {}
    for c in customers:
        signup = parse_ts(c["signup_ts"])
        if c["customer_id"] in index or signup is None or signup >= window_end:
            continue
        home = c["city"] if c["city"] in CITY_CODES else None
        index[c["customer_id"]] = (home, signup)
    return index


def _index_drivers(drivers: list[Record], vehicles: list[Record]) -> dict[str, list[_Driver]]:
    """city -> drivers who can take rides there (not suspended, with a valid vehicle)."""
    vehicle_type: dict[str, str] = {}
    vehicles_by_city: dict[str, list[str]] = {}
    for v in vehicles:
        valid = v["vehicle_type"] in VEHICLE_TYPE_BY_NAME and v["city"] in CITY_CODES
        if valid and v["vehicle_id"] not in vehicle_type:
            vehicle_type[v["vehicle_id"]] = v["vehicle_type"]
            vehicles_by_city.setdefault(v["city"], []).append(v["vehicle_id"])

    pools: dict[str, list[_Driver]] = {}
    seen: set[str] = set()
    for d in drivers:
        if d["driver_id"] in seen or d["status"] == "suspended" or d["city"] not in CITY_CODES:
            continue
        seen.add(d["driver_id"])
        joined_at = parse_ts(d["joined_at"])
        if joined_at is None:
            continue
        vehicle_id = d["vehicle_id"]
        if vehicle_id not in vehicle_type:  # missing or orphan vehicle: assign one in the city
            city_vehicles = vehicles_by_city.get(d["city"])
            if not city_vehicles:
                continue
            vehicle_id = city_vehicles[int(d["driver_id"][4:]) % len(city_vehicles)]
        pools.setdefault(d["city"], []).append(
            _Driver(d["driver_id"], vehicle_id, vehicle_type[vehicle_id], joined_at)
        )
    return pools


# --------------------------------------------------------------------------- one booking


def _surge(rng: random.Random, local_hour: int) -> float:
    if local_hour in PEAK_HOURS_IST and rng.random() < 0.4:
        return rng.choice((1.2, 1.4, 1.6, 1.8))
    if local_hour in LATE_NIGHT_HOURS_IST and rng.random() < 0.3:
        return 1.3
    return 1.0


def _find_driver(rng: random.Random, pool: list[_Driver], requested: datetime) -> _Driver | None:
    """Pick a random driver from the city who had joined before the request, if one turns up."""
    if not pool:
        return None
    for _ in range(MAX_DRIVER_TRIES):
        driver = rng.choice(pool)
        if driver.joined_at <= requested:
            return driver
    return None


def calculate_fare(city_code: str, vehicle_type: str, distance_km: float, surge: float) -> float:
    """Fare formula shared with tests (and later with SQL checks in Silver)."""
    city = CITY_BY_CODE[city_code]
    multiplier = VEHICLE_TYPE_BY_NAME[vehicle_type].fare_multiplier
    return round((city.base_fare + city.per_km_rate * distance_km) * multiplier * surge, 2)


def _cancel(rec: Record, rng: random.Random, requested: datetime, accepted: datetime) -> Record:
    by_customer = rng.random() < P_CANCEL_BY_CUSTOMER
    if by_customer and rng.random() < P_CANCEL_BEFORE_ACCEPT:
        rec.update(driver_id=None, vehicle_id=None, vehicle_type=None, accepted_at=None)
        cancelled = requested + timedelta(seconds=rng.randint(10, 240))
    else:
        cancelled = accepted + timedelta(seconds=rng.randint(30, 900))
        late = cancelled - accepted > timedelta(minutes=5)
        if by_customer and late and rng.random() < P_CANCELLATION_FEE:
            rec["fare_amount"] = CANCELLATION_FEE_INR
    reasons = CANCEL_REASONS_CUSTOMER if by_customer else CANCEL_REASONS_DRIVER
    rec.update(
        status="cancelled",
        cancelled_by="customer" if by_customer else "driver",
        cancellation_reason=rng.choice(reasons),
        cancelled_at=to_iso(cancelled),
        updated_at=to_iso(cancelled),
    )
    return rec


def _build_booking(
    seq: int,
    rng: random.Random,
    customer_id: str,
    city_code: str,
    requested: datetime,
    pool: list[_Driver],
    window_end: datetime,
) -> Record:
    local_hour = requested.astimezone(IST).hour
    surge = _surge(rng, local_hour)
    distance = round(min(max(rng.lognormvariate(math.log(6), 0.6), 1.0), 60.0), 2)

    rec: Record = dict.fromkeys(BOOKING_FIELDS)  # every field present, defaulting to None
    rec.update(
        booking_id=f"BKG-{seq:07d}",
        customer_id=customer_id,
        city=city_code,
        requested_at=to_iso(requested),
        distance_km=distance,
        surge_multiplier=surge,
        fare_amount=0.0,
        currency="INR",
        payment_method=rng.choices(PAYMENT_METHODS, weights=PAYMENT_METHOD_WEIGHTS)[0],
    )

    roll = rng.random()
    driver = _find_driver(rng, pool, requested)
    if roll < P_NO_DRIVER or driver is None:
        rec["status"] = "no_driver_found"
        rec["updated_at"] = to_iso(requested + timedelta(seconds=rng.randint(120, 300)))
        return rec

    accepted = requested + timedelta(seconds=rng.randint(20, 300))
    rec.update(
        driver_id=driver.driver_id,
        vehicle_id=driver.vehicle_id,
        vehicle_type=driver.vehicle_type,
        accepted_at=to_iso(accepted),
    )
    if roll < P_NO_DRIVER + P_CANCELLED:
        return _cancel(rec, rng, requested, accepted)

    pickup = accepted + timedelta(minutes=rng.uniform(2, 15))
    speed_kmh = rng.uniform(12, 20) if local_hour in PEAK_HOURS_IST else rng.uniform(20, 32)
    duration_min = distance / speed_kmh * 60 + rng.uniform(1, 5)
    dropoff = pickup + timedelta(minutes=duration_min)
    rec["pickup_at"] = to_iso(pickup)

    if dropoff > window_end:  # ride still running when the data window closes
        rec.update(status="in_progress", updated_at=rec["pickup_at"])
        return rec

    rec.update(
        status="completed",
        dropoff_at=to_iso(dropoff),
        duration_min=round(duration_min, 1),
        fare_amount=calculate_fare(city_code, driver.vehicle_type, distance, surge),
        updated_at=to_iso(dropoff),
    )
    return rec


# --------------------------------------------------------------------------- public API


def build_bookings(
    n: int,
    *,
    seed: int,
    end_date: date,
    history_days: int,
    customers: list[Record],
    drivers: list[Record],
    vehicles: list[Record],
) -> list[Record]:
    """Build n clean bookings that reference valid customers, drivers and vehicles."""
    rng = make_rng(seed, "bookings")
    first_day = end_date - timedelta(days=history_days - 1)
    window_end = datetime.combine(end_date, time(23, 59, 59), tzinfo=IST)

    customer_index = _index_customers(customers, window_end)
    driver_pools = _index_drivers(drivers, vehicles)
    if not customer_index or not driver_pools:
        raise ValueError("Bookings need at least one valid customer and one valid driver")

    # Draw all customers, days and hours up front: one rng.choices(k=n) call is much faster
    # than n separate calls. Customers get a random activity level, so some ride far more often.
    customer_ids = sorted(customer_index)
    activity = itertools.accumulate(rng.lognormvariate(0, 1) for _ in customer_ids)
    picked_customers = rng.choices(customer_ids, cum_weights=list(activity), k=n)

    days = [first_day + timedelta(days=i) for i in range(history_days)]
    day_weights = [
        (1 + DEMAND_GROWTH * i / history_days) * (WEEKEND_UPLIFT if d.weekday() >= 5 else 1.0)
        for i, d in enumerate(days)
    ]
    picked_days = rng.choices(days, weights=day_weights, k=n)
    picked_hours = rng.choices(range(24), weights=HOUR_WEIGHTS_IST, k=n)

    bookings: list[Record] = []
    for i in range(n):
        customer_id = picked_customers[i]
        home_city, signup = customer_index[customer_id]
        local_time = time(picked_hours[i], rng.randrange(60), rng.randrange(60))
        requested = datetime.combine(picked_days[i], local_time, tzinfo=IST)
        if requested < signup:  # nobody can book before signing up
            requested = random_datetime(rng, signup, window_end)
        city = home_city if home_city and rng.random() < P_HOME_CITY else pick_city(rng).code
        bookings.append(
            _build_booking(
                i + 1, rng, customer_id, city, requested, driver_pools.get(city, []), window_end
            )
        )
    return bookings


def _corrupt_booking(record: Record, rng: random.Random) -> None:
    choice = rng.randrange(5)
    if choice == 0:
        record["fare_amount"] = -abs(record["fare_amount"] or 100.0)  # negative fare
    elif choice == 1 and record["dropoff_at"]:
        record["pickup_at"], record["dropoff_at"] = record["dropoff_at"], record["pickup_at"]
    elif choice == 2:
        record["customer_id"] = "CUS-999999"  # orphan foreign key
    elif choice == 3:
        record["status"] = f" {record['status'].upper()} "  # needs trimming + lower-casing
    else:  # choice 4, or choice 1 on a booking without a dropoff
        record["requested_at"] = to_legacy_format(record["requested_at"])


def apply_booking_defects(records: list[Record], *, seed: int, defects: DefectRates) -> EntityBatch:
    """Inject defects into clean bookings (in place) and wrap them in an EntityBatch.

    Uses its own rng, so changing defect rates never changes the underlying clean data.
    """
    rng = make_rng(seed, "bookings-defects")
    counts = apply_defects(
        records,
        rng,
        defects,
        corrupt=_corrupt_booking,
        nullable_fields=("customer_id", "payment_method", "distance_km", "surge_multiplier"),
        mutate_duplicate=bump_updated_at,
    )
    return EntityBatch("bookings", records, BOOKING_FIELDS, "csv", counts)
