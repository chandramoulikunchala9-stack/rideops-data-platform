import os
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from rideops.config import Settings


def test_defaults_work_without_any_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [n for n in os.environ if n.startswith("RIDEOPS_")]:
        monkeypatch.delenv(name)  # ignore the developer's shell environment...
    settings = Settings(_env_file=None)  # ...and any local .env, so the test is deterministic
    assert settings.env == "local"
    assert settings.seed == 42
    assert settings.scale == "small"
    assert settings.data_dir == Path("data")
    assert settings.data_end_date == date(2026, 10, 31)


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RIDEOPS_SEED", "7")  # env vars are always strings...
    monkeypatch.setenv("RIDEOPS_SCALE", "full")
    monkeypatch.setenv("RIDEOPS_DATA_END_DATE", "2026-01-15")

    settings = Settings(_env_file=None)

    assert settings.seed == 7  # ...and pydantic converts them to the declared type
    assert settings.scale == "full"
    assert settings.data_end_date == date(2026, 1, 15)


def test_invalid_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RIDEOPS_SCALE", "gigantic")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
