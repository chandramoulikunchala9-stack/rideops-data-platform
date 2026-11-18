from pathlib import Path

import pytest

from rideops.db.queries import load_queries, parse_named_queries
from rideops.ingestion.postgres_loader import (
    LandingFile,
    SchemaMismatchError,
    _copy_csv,
    _copy_jsonl,
    discover_files,
)
from rideops.quality.checks import decide_status
from rideops.utils.retry import retry


@pytest.mark.parametrize(
    ("failed", "total", "threshold", "expected"),
    [(0, 100, 0.0, "PASS"), (1, 100, 0.02, "WARN"), (3, 100, 0.02, "FAIL"), (1, 0, 0.5, "FAIL")],
)
def test_decide_status(failed: int, total: int, threshold: float, expected: str) -> None:
    assert decide_status(failed, total, threshold) == expected


def test_retry_retries_listed_errors_then_succeeds() -> None:
    calls = []

    @retry(attempts=3, base_delay=0, retry_on=(ConnectionError,))
    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("blip")
        return "ok"

    assert flaky() == "ok"
    assert len(calls) == 3


def test_retry_does_not_retry_other_errors() -> None:
    calls = []

    @retry(attempts=5, base_delay=0, retry_on=(ConnectionError,))
    def broken() -> None:
        calls.append(1)
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        broken()
    assert len(calls) == 1


def test_retry_gives_up_after_max_attempts() -> None:
    @retry(attempts=2, base_delay=0, retry_on=(ConnectionError,))
    def always_down() -> None:
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        always_down()


def test_parse_named_queries_splits_and_rejects_duplicates() -> None:
    text = "-- header\n-- name: a\nSELECT 1;\n\n-- name: b\n-- comment\nSELECT 2;\n"
    assert parse_named_queries(text) == {"a": "SELECT 1", "b": "-- comment\nSELECT 2"}
    with pytest.raises(ValueError):
        parse_named_queries("-- name: a\nSELECT 1;\n-- name: a\nSELECT 2;")


def test_repository_analytics_queries_are_unique_and_cover_the_required_scripts() -> None:
    queries = load_queries()
    assert len(queries) >= 40
    files = sorted(p.name for p in (Path(__file__).parents[2] / "sql" / "analytics").glob("*.sql"))
    assert len(files) == 9


class _NoCopyCursor:
    """A cursor that fails the test if COPY is attempted: validation must happen first."""

    def copy(self, *_args, **_kwargs):
        raise AssertionError("COPY must not start for an invalid file")


def _landing(tmp_path: Path, entity: str, name: str, content: str) -> LandingFile:
    path = tmp_path / "landing" / entity / "batch_date=2026-10-31" / name
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    return LandingFile(entity, path, path.relative_to(tmp_path).as_posix(), "0001")


def test_csv_with_wrong_header_is_a_schema_mismatch(tmp_path: Path) -> None:
    f = _landing(tmp_path, "customers", "customers_20261031_0001.csv", "customer_id,name\nC1,x\n")
    with pytest.raises(SchemaMismatchError, match="expected columns"):
        _copy_csv(_NoCopyCursor(), f, ("customer_id", "full_name"))


def test_discover_files_finds_partitioned_files_and_batch_ids(tmp_path: Path) -> None:
    _landing(tmp_path, "bookings", "bookings_20261031_0007.csv", "x\n")
    (files,) = discover_files(tmp_path, ["bookings"])
    assert (
        files.relative_path == "landing/bookings/batch_date=2026-10-31/bookings_20261031_0007.csv"
    )
    assert files.batch_id == "0007"


class _RecordingCopy:
    def __init__(self) -> None:
        self.rows: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def write_row(self, row) -> None:
        self.rows.append(row)


class _RecordingCursor:
    def __init__(self) -> None:
        self.copy_obj = _RecordingCopy()

    def copy(self, *_args, **_kwargs):
        return self.copy_obj


def test_jsonl_with_missing_key_is_a_schema_mismatch(tmp_path: Path) -> None:
    f = _landing(tmp_path, "payments", "payments_20261031_0001.jsonl", '{"a": 1}\n{"b": 2}\n')
    cur = _RecordingCursor()
    with pytest.raises(SchemaMismatchError, match="line 2"):
        _copy_jsonl(cur, f, ("a",))
    assert len(cur.copy_obj.rows) == 1  # rows before the bad line are rolled back by the caller
