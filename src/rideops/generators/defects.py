"""Deliberate data defects, so the Silver layer has real cleaning work to do.

Each function changes exactly `round(len(records) * rate)` records and returns that count.
Exact counts (rather than "roughly 2%") make the defects testable and let us later prove
that the Silver layer caught every one of them.
"""

import copy
import random
from collections.abc import Callable, Sequence, Set
from dataclasses import dataclass
from typing import Any

Record = dict[str, Any]


@dataclass(frozen=True)
class DefectRates:
    missing: float = 0.02  # a nullable field is blanked out
    duplicate: float = 0.01  # a record is sent twice (sometimes with a later update)
    invalid: float = 0.005  # a value breaks a business rule or format

    def __post_init__(self) -> None:
        for name in ("missing", "duplicate", "invalid"):
            value = getattr(self, name)
            if not 0.0 <= value < 1.0:
                raise ValueError(f"Defect rate {name!r} must be in [0, 1), got {value}")


NO_DEFECTS = DefectRates(missing=0.0, duplicate=0.0, invalid=0.0)


def pick_indices(
    n: int, rate: float, rng: random.Random, exclude: Set[int] = frozenset()
) -> list[int]:
    """Choose round(n * rate) distinct positions out of n, skipping `exclude`.

    Deterministic for a given rng state.
    """
    candidates = [i for i in range(n) if i not in exclude]
    k = min(round(n * rate), len(candidates))
    return sorted(rng.sample(candidates, k))


def inject_missing(
    records: list[Record],
    fields: Sequence[str],
    rate: float,
    rng: random.Random,
    exclude: Set[int] = frozenset(),
) -> list[int]:
    """Set one randomly chosen field to None in a sample of records.

    Returns the positions that were changed.
    """
    indices = pick_indices(len(records), rate, rng, exclude)
    for i in indices:
        records[i][rng.choice(fields)] = None
    return indices


def inject_invalid(
    records: list[Record],
    rate: float,
    rng: random.Random,
    corrupt: Callable[[Record, random.Random], None],
) -> list[int]:
    """Apply an entity-specific `corrupt` function to a sample of records, in place.

    Returns the positions that were changed.
    """
    indices = pick_indices(len(records), rate, rng)
    for i in indices:
        corrupt(records[i], rng)
    return indices


def inject_duplicates(
    records: list[Record],
    rate: float,
    rng: random.Random,
    mutate: Callable[[Record, random.Random], None] | None = None,
) -> int:
    """Append copies of sampled records, then shuffle so duplicates are not all at the end.

    If `mutate` is given, about half of the copies are changed by it. This mimics a source
    system re-sending a record after an update, so Silver must keep the *latest* version,
    not just drop exact copies.
    """
    indices = pick_indices(len(records), rate, rng)
    for i in indices:
        dup = copy.deepcopy(records[i])
        if mutate is not None and rng.random() < 0.5:
            mutate(dup, rng)
        records.append(dup)
    rng.shuffle(records)
    return len(indices)


def apply_defects(
    records: list[Record],
    rng: random.Random,
    rates: DefectRates,
    *,
    corrupt: Callable[[Record, random.Random], None],
    nullable_fields: Sequence[str],
    mutate_duplicate: Callable[[Record, random.Random], None] | None = None,
) -> dict[str, int]:
    """Inject all three defect types in a fixed order and return how many of each were injected.

    Order matters:
      1. invalid first;
      2. missing skips rows already made invalid, so one defect never hides another
         and the counts stay exact;
      3. duplicates last, so a corrupted row can also be re-sent (as happens in real feeds).
    """
    invalid = inject_invalid(records, rates.invalid, rng, corrupt)
    missing = inject_missing(records, nullable_fields, rates.missing, rng, exclude=set(invalid))
    duplicates = inject_duplicates(records, rates.duplicate, rng, mutate_duplicate)
    return {"invalid": len(invalid), "missing": len(missing), "duplicate": duplicates}
