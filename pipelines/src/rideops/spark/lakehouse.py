"""Lakehouse layout and shared column helpers.

<data_dir>/lakehouse/
    bronze/<entity>         raw records + ingestion metadata (append-only)
    silver/<entity>         cleaned, deduplicated, validated (MERGE upserts)
    silver/_quarantine      rejected rows with the reason
    gold/<table>            business aggregates
    _checkpoints/<query>    streaming progress (which files/versions were processed)
    _state/<name>.json      small job state, e.g. the last silver version gold consumed
"""

import json
from dataclasses import dataclass
from pathlib import Path

from pyspark.sql import Column, SparkSession
from pyspark.sql import functions as F

ISO_PATTERN = "yyyy-MM-dd'T'HH:mm:ss'Z'"
LEGACY_PATTERN = "dd/MM/yyyy HH:mm"
LEGACY_REGEX = r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$"
BUSINESS_TZ = "Asia/Kolkata"


@dataclass(frozen=True)
class Lakehouse:
    data_dir: Path

    @property
    def root(self) -> Path:
        return self.data_dir / "lakehouse"

    def landing(self, entity: str) -> str:
        return str(self.data_dir / "landing" / entity)

    def bronze(self, entity: str) -> str:
        return str(self.root / "bronze" / entity)

    def silver(self, entity: str) -> str:
        return str(self.root / "silver" / entity)

    @property
    def quarantine(self) -> str:
        return str(self.root / "silver" / "_quarantine")

    def gold(self, table: str) -> str:
        return str(self.root / "gold" / table)

    def checkpoint(self, name: str) -> str:
        return str(self.root / "_checkpoints" / name)

    # ---------------------------------------------------------------- small JSON job state
    def read_state(self, name: str) -> dict:
        path = self.root / "_state" / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def write_state(self, name: str, state: dict) -> None:
        path = self.root / "_state" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def table_exists(spark: SparkSession, path: str) -> bool:
    from delta.tables import DeltaTable

    return DeltaTable.isDeltaTable(spark, path)


# ---------------------------------------------------------------- column helpers
# Spark 4 runs with ANSI mode on: a plain cast/to_timestamp of bad input raises an error.
# The try_* variants return NULL instead, so a bad value becomes a data-quality finding,
# not a failed job.


def parse_ts(col: str | Column) -> Column:
    c = F.col(col) if isinstance(col, str) else col
    return F.coalesce(F.try_to_timestamp(c, F.lit(ISO_PATTERN)),
                      F.try_to_timestamp(c, F.lit(LEGACY_PATTERN)))  # fmt: skip


def is_legacy_ts(col: str) -> Column:
    return F.coalesce(F.col(col).rlike(LEGACY_REGEX), F.lit(False))


def to_num(col: str, dtype: str = "double") -> Column:
    return F.trim(F.col(col)).try_cast(dtype)


def blank_to_null(col: str) -> Column:
    trimmed = F.trim(F.col(col))
    return F.when(trimmed == "", None).otherwise(trimmed)


def flags(*conditions: tuple[Column, str]) -> Column:
    """Array of flag names whose condition is true (NULLs removed), like SQL array_remove."""
    return F.array_compact(F.array(*[F.when(cond, F.lit(name)) for cond, name in conditions]))


def business_date(col: str) -> Column:
    """The local (IST) calendar date of a UTC timestamp, matching core.bookings.booking_date."""
    return F.to_date(F.from_utc_timestamp(F.col(col), BUSINESS_TZ))
