"""Helpers shared by the generators: seeded randomness, timestamps, the EntityBatch result type."""

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal

from faker import Faker

from rideops.generators.reference import CITIES, City

FileFormat = Literal["csv", "jsonl"]

# India Standard Time. All cities are in India, so demand patterns (rush hours) follow IST,
# but every stored timestamp is converted to UTC.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

_CITY_WEIGHTS = [c.weight for c in CITIES]
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_LEGACY_FORMAT = "%d/%m/%Y %H:%M"


@dataclass
class EntityBatch:
    """The output of one generator: records plus everything needed to write and describe them."""

    name: str
    records: list[dict[str, Any]]
    fieldnames: tuple[str, ...]
    file_format: FileFormat
    defects: dict[str, int] = field(default_factory=dict)
    # If set, records are split into one file per date of this timestamp field
    # (e.g. events by ingest_ts) instead of a single file for the whole batch.
    partition_by: str | None = None


def make_rng(seed: int, entity: str) -> random.Random:
    """A separate random generator per entity, derived from the global seed.

    Seeding with a string like "42-customers" gives each entity its own independent but
    reproducible sequence. Adding a new entity later therefore does not change the data of
    existing ones. (String seeds are hashed deterministically, unlike Python's hash().)
    """
    return random.Random(f"{seed}-{entity}")


def make_faker(seed: int, entity: str) -> Faker:
    fake = Faker("en_IN")
    fake.seed_instance(f"{seed}-{entity}")
    return fake


def end_of_day_utc(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59), tzinfo=timezone.utc)


def random_datetime(rng: random.Random, start: datetime, end: datetime) -> datetime:
    span = int((end - start).total_seconds())
    return start + timedelta(seconds=rng.randint(0, span))


def to_iso(ts: datetime) -> str:
    """Canonical timestamp format for all generated data: ISO 8601 in UTC.

    Example: 2026-10-31T08:15:00Z
    """
    # isoformat() is implemented in C and ~3x faster than strftime(); this function runs
    # ~600k times per full-scale run. timespec="seconds" truncates microseconds like %S does.
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")[:19] + "Z"


def to_legacy_format(iso_ts: str) -> str:
    """A non-standard format some 'legacy' source rows use (e.g. 31/10/2026 08:15).

    Used as a defect: Silver must recognise and standardise or reject these.
    """
    return datetime.strptime(iso_ts, _ISO_FORMAT).strftime(_LEGACY_FORMAT)


def parse_ts(value: str | None) -> datetime | None:
    """Parse a generated timestamp (ISO or legacy format) into an aware UTC datetime.

    Returns None for missing or unparseable values instead of raising, because callers
    here use it to *skip* bad rows, not to validate them (validation is Silver's job).
    """
    if not value:
        return None
    try:
        # Fast path for the canonical format. fromisoformat() is C code; on Python 3.10 it
        # does not accept a trailing "Z", so strip it.
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1]).replace(tzinfo=timezone.utc)
        return datetime.strptime(value, _LEGACY_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def pick_city(rng: random.Random) -> City:
    """Pick a city, weighted by its share of platform activity."""
    return rng.choices(CITIES, weights=_CITY_WEIGHTS)[0]


def bump_updated_at(record: dict[str, Any], rng: random.Random) -> None:
    """Duplicate mutation: the same record re-sent 1-72 hours later with a newer updated_at."""
    updated = parse_ts(record.get("updated_at"))
    if updated is not None:
        record["updated_at"] = to_iso(updated + timedelta(hours=rng.randint(1, 72)))
