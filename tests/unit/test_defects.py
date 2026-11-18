import random

import pytest

from rideops.generators.defects import (
    DefectRates,
    apply_defects,
    inject_duplicates,
    pick_indices,
)


def _records(n: int) -> list[dict]:
    return [{"id": i, "name": f"n{i}", "value": i * 10, "updated_at": None} for i in range(n)]


def test_pick_indices_returns_exact_count_and_respects_exclude() -> None:
    picked = pick_indices(1000, 0.05, random.Random(1), exclude={0, 1, 2})
    assert len(picked) == 50
    assert len(set(picked)) == 50  # no position chosen twice
    assert not {0, 1, 2} & set(picked)


def test_pick_indices_is_reproducible() -> None:
    assert pick_indices(500, 0.1, random.Random(9)) == pick_indices(500, 0.1, random.Random(9))


@pytest.mark.parametrize("bad_rate", [-0.1, 1.0, 2.5])
def test_defect_rates_are_validated(bad_rate: float) -> None:
    with pytest.raises(ValueError):
        DefectRates(missing=bad_rate)


def test_inject_duplicates_adds_copies_of_existing_ids() -> None:
    records = _records(200)
    added = inject_duplicates(records, 0.05, random.Random(3))
    assert added == 10
    assert len(records) == 210
    assert len({r["id"] for r in records}) == 200  # every duplicate repeats an existing id


def test_apply_defects_never_blanks_a_row_that_was_made_invalid() -> None:
    records = _records(1000)

    def corrupt(record: dict, rng: random.Random) -> None:
        record["value"] = -1

    counts = apply_defects(
        records,
        random.Random(5),
        DefectRates(missing=0.2, duplicate=0.0, invalid=0.2),
        corrupt=corrupt,
        nullable_fields=("name",),
    )

    invalid_rows = [r for r in records if r["value"] == -1]
    missing_rows = [r for r in records if r["name"] is None]
    assert counts == {"invalid": 200, "missing": 200, "duplicate": 0}
    assert len(invalid_rows) == 200
    assert len(missing_rows) == 200
    assert not [r for r in invalid_rows if r["name"] is None]  # no overlap
