"""Apply pipelines/sql/ddl/*.sql in order and seed reference tables.

Each DDL file is applied once and recorded in ops.schema_migrations with its checksum.
Changing an already-applied file is reported, because the change would never reach
databases that already ran it: schema changes belong in a new, higher-numbered file.
"""

import hashlib
import logging
from pathlib import Path

import psycopg

from rideops.config import SQL_DIR
from rideops.generators.reference import CITIES, VEHICLE_TYPES

logger = logging.getLogger(__name__)

BOOTSTRAP = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.schema_migrations (
    version     text        PRIMARY KEY,
    checksum    char(64)    NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_migrations(conn: psycopg.Connection, ddl_dir: Path = SQL_DIR / "ddl") -> list[str]:
    """Apply pending DDL files. Returns the versions applied in this call."""
    with conn.transaction():
        conn.execute(BOOTSTRAP)
    applied = dict(conn.execute("SELECT version, checksum FROM ops.schema_migrations").fetchall())

    newly_applied = []
    for path in sorted(ddl_dir.glob("*.sql")):
        version, checksum = path.stem, _checksum(path)
        if version in applied:
            if applied[version] != checksum:
                logger.warning(
                    "Migration %s changed after it was applied; add a new migration instead",
                    version,
                )
            continue
        with conn.transaction():  # a failing file leaves no partial schema behind
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO ops.schema_migrations (version, checksum) VALUES (%s, %s)",
                (version, checksum),
            )
        logger.info("Applied migration %s", version)
        newly_applied.append(version)
    return newly_applied


def seed_reference_data(conn: psycopg.Connection) -> None:
    """Load cities and vehicle types from the Python reference module (single source of truth)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO core.cities (city_code, city_name, state_code, base_fare, per_km_rate)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (city_code) DO UPDATE SET
                city_name = excluded.city_name, state_code = excluded.state_code,
                base_fare = excluded.base_fare, per_km_rate = excluded.per_km_rate
            """,
            [(c.code, c.name, c.state_code, c.base_fare, c.per_km_rate) for c in CITIES],
        )
        cur.executemany(
            """
            INSERT INTO core.vehicle_types (vehicle_type, seats, fare_multiplier)
            VALUES (%s, %s, %s)
            ON CONFLICT (vehicle_type) DO UPDATE SET
                seats = excluded.seats, fare_multiplier = excluded.fare_multiplier
            """,
            [(t.name, t.seats, t.fare_multiplier) for t in VEHICLE_TYPES],
        )
