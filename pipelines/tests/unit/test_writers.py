import csv
import json
from datetime import date
from pathlib import Path

from rideops.generators.writers import landing_path, write_csv, write_jsonl


def test_landing_path_follows_the_naming_convention(tmp_path: Path) -> None:
    path = landing_path(tmp_path, "bookings", date(2026, 10, 31), "0007", "csv")
    assert path.relative_to(tmp_path).as_posix() == (
        "landing/bookings/batch_date=2026-10-31/bookings_20261031_0007.csv"
    )


def test_csv_writes_none_as_empty_cell_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "out" / "x.csv"
    write_csv([{"a": 1, "b": None}, {"a": 2, "b": "y"}], ("a", "b"), path)

    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows == [{"a": "1", "b": ""}, {"a": "2", "b": "y"}]
    assert [p.name for p in path.parent.iterdir()] == ["x.csv"]  # temp file was renamed away


def test_jsonl_keeps_types_one_object_per_line(tmp_path: Path) -> None:
    path = write_jsonl(
        [{"n": 1, "ok": True, "v": None}, {"n": 2, "ok": False, "v": 1.5}], tmp_path / "x.jsonl"
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [
        {"n": 1, "ok": True, "v": None},
        {"n": 2, "ok": False, "v": 1.5},
    ]
