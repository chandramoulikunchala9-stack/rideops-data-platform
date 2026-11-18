"""Incremental, file-based ingestion of landing files into PostgreSQL `raw` tables.

For every file under <data_dir>/landing/<entity>/batch_date=*/:
  1. skip it if ops.ingestion_log already says it was loaded (incremental ingestion);
  2. validate its schema against the source contract (the generator's field list);
  3. COPY its rows into raw.<entity> with source file, batch id and row number;
  4. record the file (size, sha256, row count) in ops.ingestion_log in the SAME transaction,
     so data and bookkeeping are committed together or not at all.
Transient database errors are retried; a schema mismatch is recorded as a failed file and
the loader moves on to the next file instead of stopping the whole run.
"""

import csv
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import orjson
import psycopg

from rideops.generators.bookings import BOOKING_FIELDS
from rideops.generators.entities import CUSTOMER_FIELDS, DRIVER_FIELDS, VEHICLE_FIELDS
from rideops.generators.ops import COST_FIELDS, RUN_FIELDS
from rideops.generators.payments import PAYMENT_FIELDS
from rideops.utils.retry import retry

logger = logging.getLogger(__name__)

# Load order matters only for readability; foreign keys are enforced later, in core.
CONTRACTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "vehicles": ("jsonl", VEHICLE_FIELDS),
    "drivers": ("csv", DRIVER_FIELDS),
    "customers": ("csv", CUSTOMER_FIELDS),
    "bookings": ("csv", BOOKING_FIELDS),
    "payments": ("jsonl", PAYMENT_FIELDS),
    "pipeline_runs": ("jsonl", RUN_FIELDS),
    "cloud_costs": ("csv", COST_FIELDS),
}


class SchemaMismatchError(ValueError):
    """The file's columns/keys do not match the agreed source contract."""


@dataclass(frozen=True)
class LandingFile:
    entity: str
    path: Path
    relative_path: str
    batch_id: str


@dataclass
class LoadSummary:
    loaded: int = 0
    skipped: int = 0
    failed: int = 0
    rows: int = 0
    failures: list[str] = field(default_factory=list)


def discover_files(data_dir: Path, entities: list[str] | None = None) -> list[LandingFile]:
    files = []
    for entity, (fmt, _) in CONTRACTS.items():
        if entities and entity not in entities:
            continue
        for path in sorted((data_dir / "landing" / entity).glob(f"batch_date=*/*.{fmt}")):
            files.append(
                LandingFile(
                    entity=entity,
                    path=path,
                    relative_path=path.relative_to(data_dir).as_posix(),
                    batch_id=path.stem.rsplit("_", 1)[-1],  # customers_20261031_0001 -> 0001
                )
            )
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_csv(cur: psycopg.Cursor, f: LandingFile, columns: tuple[str, ...]) -> int:
    with f.path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = tuple(next(reader, ()))
        if header != columns:
            raise SchemaMismatchError(
                f"Schema mismatch in {f.relative_path}: expected columns {list(columns)}, "
                f"got {list(header)}"
            )
        target = ", ".join([*columns, "_source_file", "_batch_id", "_row_number"])
        rows = 0
        with cur.copy(f"COPY raw.{f.entity} ({target}) FROM STDIN") as copy:
            for rows, values in enumerate(reader, start=1):
                if len(values) != len(columns):
                    raise SchemaMismatchError(
                        f"Schema mismatch in {f.relative_path} line {rows + 1}: "
                        f"expected {len(columns)} values, got {len(values)}"
                    )
                copy.write_row([*values, f.relative_path, f.batch_id, rows])
    return rows


def _copy_jsonl(cur: psycopg.Cursor, f: LandingFile, keys: tuple[str, ...]) -> int:
    expected = set(keys)
    rows = 0
    with (
        f.path.open(encoding="utf-8") as fh,
        cur.copy(
            f"COPY raw.{f.entity} (doc, _source_file, _batch_id, _row_number) FROM STDIN"
        ) as copy,
    ):
        for rows, line in enumerate(fh, start=1):
            try:
                record = orjson.loads(line)
            except orjson.JSONDecodeError as exc:
                raise SchemaMismatchError(
                    f"Schema mismatch in {f.relative_path} line {rows}: invalid JSON ({exc})"
                ) from exc
            if set(record) != expected:
                raise SchemaMismatchError(
                    f"Schema mismatch in {f.relative_path} line {rows}: "
                    f"missing {sorted(expected - set(record))}, "
                    f"unexpected {sorted(set(record) - expected)}"
                )
            copy.write_row([line.rstrip("\n"), f.relative_path, f.batch_id, rows])
    return rows


@retry(attempts=3, base_delay=1.0, retry_on=(psycopg.OperationalError,))
def load_file(conn: psycopg.Connection, f: LandingFile) -> int:
    """Load one file and log it, atomically. Returns the number of rows loaded."""
    fmt, columns = CONTRACTS[f.entity]
    size, sha = f.path.stat().st_size, _sha256(f.path)
    with conn.transaction(), conn.cursor() as cur:
        rows = _copy_csv(cur, f, columns) if fmt == "csv" else _copy_jsonl(cur, f, columns)
        cur.execute(
            """
            INSERT INTO ops.ingestion_log
                (file_path, entity, batch_id, status, rows_loaded, file_bytes, file_sha256)
            VALUES (%s, %s, %s, 'loaded', %s, %s, %s)
            ON CONFLICT (file_path) DO UPDATE SET
                status = 'loaded', rows_loaded = excluded.rows_loaded,
                file_bytes = excluded.file_bytes, file_sha256 = excluded.file_sha256,
                error_message = NULL, attempts = ops.ingestion_log.attempts + 1,
                loaded_at = now()
            """,
            (f.relative_path, f.entity, f.batch_id, rows, size, sha),
        )
    return rows


def _record_failure(conn: psycopg.Connection, f: LandingFile, error: str) -> None:
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO ops.ingestion_log
                (file_path, entity, batch_id, status, file_bytes, file_sha256, error_message)
            VALUES (%s, %s, %s, 'failed', %s, %s, %s)
            ON CONFLICT (file_path) DO UPDATE SET
                status = 'failed', error_message = excluded.error_message,
                attempts = ops.ingestion_log.attempts + 1, loaded_at = now()
            """,
            (f.relative_path, f.entity, f.batch_id, f.path.stat().st_size, _sha256(f.path), error),
        )


def load_landing(
    conn: psycopg.Connection, data_dir: Path, entities: list[str] | None = None
) -> LoadSummary:
    summary = LoadSummary()
    already_loaded = {
        row[0]
        for row in conn.execute("SELECT file_path FROM ops.ingestion_log WHERE status = 'loaded'")
    }
    for f in discover_files(data_dir, entities):
        if f.relative_path in already_loaded:
            summary.skipped += 1
            continue
        try:
            rows = load_file(conn, f)
        except SchemaMismatchError as exc:
            logger.error("%s", exc)
            _record_failure(conn, f, str(exc))
            summary.failed += 1
            summary.failures.append(str(exc))
            continue
        summary.loaded += 1
        summary.rows += rows
        logger.info("Loaded %-60s rows=%d", f.relative_path, rows)
    return summary
