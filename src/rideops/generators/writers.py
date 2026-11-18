"""Write generated batches into the landing zone.

Landing zone layout (the same convention is used for S3 keys in Phase 6):

    <data_dir>/landing/<entity>/batch_date=YYYY-MM-DD/<entity>_<YYYYMMDD>_<batch_id>.<ext>

`batch_date=...` is a Hive-style partition folder: Spark and Databricks Auto Loader recognise it
and expose `batch_date` as a column, and they can skip whole folders when a query filters on it.
"""

import csv
import json
import os
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

from rideops.generators.common import EntityBatch


def landing_path(data_dir: Path, entity: str, batch_date: date, batch_id: str, ext: str) -> Path:
    return (
        data_dir
        / "landing"
        / entity
        / f"batch_date={batch_date.isoformat()}"
        / f"{entity}_{batch_date:%Y%m%d}_{batch_id}.{ext}"
    )


def _atomic_write(path: Path, write_fn) -> None:
    """Write to a hidden temp file, then rename it into place.

    A file-watching ingester (like Auto Loader) could otherwise pick up a half-written file.
    Spark ignores names starting with '.' or '_', and os.replace() is atomic on the same disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            write_fn(fh)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)  # only exists if writing failed


def write_csv(records: Iterable[dict[str, Any]], fieldnames: tuple[str, ...], path: Path) -> Path:
    """None values are written as empty cells, which is how missing values usually arrive in CSV."""

    def _write(fh) -> None:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    _atomic_write(path, _write)
    return path


def write_jsonl(records: Iterable[dict[str, Any]], path: Path) -> Path:
    """JSON Lines: one JSON object per line.

    Unlike CSV it keeps types (numbers, booleans, null), and it can be read line by line.
    """

    def _write(fh) -> None:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    _atomic_write(path, _write)
    return path


def write_batch(batch: EntityBatch, data_dir: Path, batch_date: date, batch_id: str) -> Path:
    path = landing_path(data_dir, batch.name, batch_date, batch_id, batch.file_format)
    if batch.file_format == "csv":
        return write_csv(batch.records, batch.fieldnames, path)
    return write_jsonl(batch.records, path)
