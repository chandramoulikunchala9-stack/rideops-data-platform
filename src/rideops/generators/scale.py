"""Dataset size presets.

`small` is for quick local runs and tests, `full` meets the project volume targets,
and `large` is for Spark performance testing.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScaleProfile:
    name: str
    customers: int
    drivers: int
    vehicles: int
    bookings: int  # used from milestone 1b
    history_days: int  # how many days of activity end at `data_end_date`


SCALE_PROFILES: dict[str, ScaleProfile] = {
    "small": ScaleProfile(
        "small", customers=1_000, drivers=200, vehicles=100, bookings=10_000, history_days=30
    ),
    "full": ScaleProfile(
        "full", customers=10_000, drivers=2_000, vehicles=1_000, bookings=100_000, history_days=90
    ),
    "large": ScaleProfile(
        "large",
        customers=100_000,
        drivers=20_000,
        vehicles=10_000,
        bookings=2_000_000,
        history_days=180,
    ),
}


def get_scale_profile(name: str) -> ScaleProfile:
    try:
        return SCALE_PROFILES[name]
    except KeyError:
        valid = ", ".join(SCALE_PROFILES)
        raise ValueError(f"Unknown scale {name!r}. Choose one of: {valid}") from None
