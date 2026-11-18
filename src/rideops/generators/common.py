"""Helpers shared by the generators: seeded randomness, timestamps, the EntityBatch result type."""

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal

from faker import Faker

FileFormat = Literal["csv", "jsonl"]


@dataclass
class EntityBatch:
    """The output of one generator: records plus everything needed to write and describe them."""

    name: str
    records: list[dict[str, Any]]
    fieldnames: tuple[str, ...]
    file_format: FileFormat
    defects: dict[str, int] = field(default_factory=dict)


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
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_legacy_format(iso_ts: str) -> str:
    """A non-standard format some 'legacy' source rows use (e.g. 31/10/2026 08:15).

    Used as a defect: Silver must recognise and standardise or reject these.
    """
    ts = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ")
    return ts.strftime("%d/%m/%Y %H:%M")
