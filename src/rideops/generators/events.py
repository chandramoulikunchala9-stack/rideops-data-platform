"""Event streams derived from clean bookings.

ride_events  - one event per step of a ride's life cycle (what the driver/rider apps emit).
app_events   - customer app interactions: sessions that end in a booking, and abandoned ones.

Every event carries two timestamps:
  event_ts  - when it happened on the device (event time);
  ingest_ts - when it reached the platform (processing time).
Usually ingest_ts is seconds later, but a few percent arrive minutes to hours late
(phone offline, batch upload). Streaming jobs must use event_ts with a watermark to place
these correctly. Files are partitioned by *ingest* date, so a late event can land in the
next day's file.
"""

import math
import random
from datetime import datetime, timedelta

from rideops.generators.common import EntityBatch, make_rng, parse_ts, to_iso, to_legacy_format
from rideops.generators.defects import DefectRates, Record, apply_defects
from rideops.generators.reference import (
    APP_PLATFORM_WEIGHTS,
    APP_PLATFORMS,
    APP_VERSIONS,
    CITY_CENTERS,
)

RIDE_EVENT_FIELDS = (
    "event_id", "event_type", "booking_id", "customer_id", "driver_id", "city",
    "latitude", "longitude", "event_ts", "ingest_ts",
)  # fmt: skip
APP_EVENT_FIELDS = (
    "event_id", "session_id", "customer_id", "event_type", "platform", "app_version", "city",
    "event_ts", "ingest_ts",
)  # fmt: skip

RIDE_EVENT_TYPES = (
    "ride_requested", "driver_assigned", "ride_started", "ride_completed", "ride_cancelled",
    "no_driver_found",
)  # fmt: skip
P_LATE_RIDE_EVENT = 0.03
P_LATE_APP_EVENT = 0.02
P_ABANDONED_SESSION = 0.25  # extra sessions (per booking) that never become a booking
P_APP_CRASH = 0.005


def _event_id(rng: random.Random) -> str:
    """A UUID-formatted ID from our seeded rng, so IDs are unique *and* reproducible.

    Formatting 128 random bits directly is several times faster than building uuid.UUID
    objects, which matters at ~850k IDs per full-scale run.
    """
    h = f"{rng.getrandbits(128):032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def _ingest_ts(rng: random.Random, event_ts: datetime, p_late: float) -> datetime:
    if rng.random() < p_late:
        return event_ts + timedelta(minutes=rng.uniform(5, 360))  # late-arriving
    return event_ts + timedelta(seconds=rng.uniform(1, 15))


def _point_near(rng: random.Random, lat: float, lon: float, spread_deg: float) -> tuple:
    return round(lat + rng.gauss(0, spread_deg), 6), round(lon + rng.gauss(0, spread_deg), 6)


def _offset(rng: random.Random, lat: float, lon: float, km: float) -> tuple[float, float]:
    """A point roughly `km` away in a random direction (1 degree latitude ~ 111 km)."""
    angle = rng.uniform(0, 2 * math.pi)
    dlat = km / 111.0 * math.cos(angle)
    dlon = km / (111.0 * math.cos(math.radians(lat))) * math.sin(angle)
    return round(lat + dlat, 6), round(lon + dlon, 6)


# --------------------------------------------------------------------------- ride events


def build_ride_events(bookings: list[Record], *, seed: int) -> list[Record]:
    rng = make_rng(seed, "ride_events")
    events: list[Record] = []
    for b in bookings:
        pickup = _point_near(rng, *CITY_CENTERS[b["city"]], spread_deg=0.05)
        dropoff = _offset(rng, *pickup, km=b["distance_km"] or 1.0)
        steps = [("ride_requested", b["requested_at"], pickup)]
        if b["status"] == "no_driver_found":
            steps.append(("no_driver_found", b["updated_at"], pickup))
        if b["accepted_at"]:
            steps.append(("driver_assigned", b["accepted_at"], pickup))
        if b["pickup_at"]:
            steps.append(("ride_started", b["pickup_at"], pickup))
        if b["dropoff_at"]:
            steps.append(("ride_completed", b["dropoff_at"], dropoff))
        if b["cancelled_at"]:
            steps.append(("ride_cancelled", b["cancelled_at"], pickup))

        for event_type, ts, (lat, lon) in steps:
            event_ts = parse_ts(ts)
            events.append({
                "event_id": _event_id(rng),
                "event_type": event_type,
                "booking_id": b["booking_id"],
                "customer_id": b["customer_id"],
                "driver_id": b["driver_id"] if event_type != "ride_requested" else None,
                "city": b["city"],
                "latitude": lat,
                "longitude": lon,
                "event_ts": ts,
                "ingest_ts": to_iso(_ingest_ts(rng, event_ts, P_LATE_RIDE_EVENT)),
            })  # fmt: skip
    return events


def _corrupt_ride_event(record: Record, rng: random.Random) -> None:
    choice = rng.randrange(3)
    if choice == 0:
        record["latitude"], record["longitude"] = 0.0, 0.0  # "null island": GPS failure
    elif choice == 1:
        record["event_type"] = "ride_teleported"  # unknown event type
    else:
        record["event_ts"] = to_legacy_format(record["event_ts"])


def generate_ride_events(bookings: list[Record], *, seed: int, defects: DefectRates) -> EntityBatch:
    records = build_ride_events(bookings, seed=seed)
    counts = apply_defects(
        records,
        make_rng(seed, "ride_events-defects"),
        defects,
        corrupt=_corrupt_ride_event,
        nullable_fields=("latitude", "longitude", "driver_id"),
        mutate_duplicate=None,  # at-least-once delivery: exact re-sends of the same event
    )
    return EntityBatch(
        "ride_events", records, RIDE_EVENT_FIELDS, "jsonl", counts, partition_by="ingest_ts"
    )


# --------------------------------------------------------------------------- app events


def _session(
    rng: random.Random, customer_id: str, city: str, anchor: datetime, steps: list[tuple]
) -> list[Record]:
    """Events of one app session. `steps` = [(event_type, seconds relative to anchor), ...]."""
    session_id = _event_id(rng)
    platform = rng.choices(APP_PLATFORMS, weights=APP_PLATFORM_WEIGHTS)[0]
    version = rng.choice(APP_VERSIONS)
    if rng.random() < P_APP_CRASH:
        steps = [*steps, ("app_crashed", steps[-1][1] + rng.randint(1, 30))]
    out = []
    for event_type, offset_s in steps:
        event_ts = anchor + timedelta(seconds=offset_s)
        out.append({
            "event_id": _event_id(rng),
            "session_id": session_id,
            "customer_id": customer_id,
            "event_type": event_type,
            "platform": platform,
            "app_version": version,
            "city": city,
            "event_ts": to_iso(event_ts),
            "ingest_ts": to_iso(_ingest_ts(rng, event_ts, P_LATE_APP_EVENT)),
        })  # fmt: skip
    return out


def build_app_events(bookings: list[Record], *, seed: int) -> list[Record]:
    rng = make_rng(seed, "app_events")
    events: list[Record] = []
    for b in bookings:
        requested = parse_ts(b["requested_at"])
        steps = [
            ("app_opened", -rng.randint(60, 300)),
            ("fare_estimate_viewed", -rng.randint(20, 59)),
            ("booking_requested", 0),
        ]
        if b["cancelled_by"] == "customer":
            cancelled = parse_ts(b["cancelled_at"])
            steps.append(("booking_cancelled", int((cancelled - requested).total_seconds())))
        events.extend(_session(rng, b["customer_id"], b["city"], requested, steps))

        if rng.random() < P_ABANDONED_SESSION:  # the customer looked but did not book
            anchor = requested - timedelta(hours=rng.uniform(1, 48))
            abandoned = [("app_opened", 0)]
            if rng.random() < 0.6:
                abandoned.append(("fare_estimate_viewed", rng.randint(10, 90)))
            events.extend(_session(rng, b["customer_id"], b["city"], anchor, abandoned))
    return events


def _corrupt_app_event(record: Record, rng: random.Random) -> None:
    if rng.random() < 0.5:
        record["platform"] = "ANDROID "  # needs normalising
    else:
        record["app_version"] = "unknown"


def generate_app_events(bookings: list[Record], *, seed: int, defects: DefectRates) -> EntityBatch:
    records = build_app_events(bookings, seed=seed)
    counts = apply_defects(
        records,
        make_rng(seed, "app_events-defects"),
        defects,
        corrupt=_corrupt_app_event,
        nullable_fields=("platform", "app_version", "city"),
        mutate_duplicate=None,
    )
    return EntityBatch(
        "app_events", records, APP_EVENT_FIELDS, "jsonl", counts, partition_by="ingest_ts"
    )
