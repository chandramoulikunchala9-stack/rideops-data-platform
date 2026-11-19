"""API tests run against a real, freshly built PostgreSQL database (`rideops_api_test`)."""

from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from rideops.config import ApiSettings, PostgresSettings
from rideops.db.connection import connect
from rideops.db.migrate import apply_migrations, seed_reference_data
from rideops.db.pipelines import run_pipeline
from rideops.generators.__main__ import main as generate
from rideops.monitoring.alerts import run_alerting
from rideops.monitoring.incidents import seed_incidents
from rideops_api.db import Database
from rideops_api.main import create_app
from rideops_api.routers.monitoring import get_api_settings
from rideops_api.runner import JobRunner

TEST_DB = "rideops_api_test"


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/api" in item.nodeid.replace("\\", "/"):
            item.add_marker(pytest.mark.postgres)


@pytest.fixture(scope="session")
def pg(tmp_path_factory: pytest.TempPathFactory) -> PostgresSettings:
    try:
        base = PostgresSettings()
        with psycopg.connect(base.conninfo(), autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
            admin.execute(f"CREATE DATABASE {TEST_DB}")
    except (psycopg.OperationalError, ValueError) as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    settings = base.model_copy(update={"db": TEST_DB})

    data_dir: Path = tmp_path_factory.mktemp("api_data")
    assert generate(["--scale", "small", "--seed", "11", "--data-dir", str(data_dir)]) == 0
    from rideops.config import get_settings

    # Point the pipelines at the test data only while building the fixture, so the
    # environment change cannot leak into other test modules.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("RIDEOPS_DATA_DIR", str(data_dir))
        get_settings.cache_clear()
        with connect(settings) as conn:
            apply_migrations(conn)
            seed_reference_data(conn)
        for name in ("pg_load_raw", "pg_transform_core", "pg_build_analytics", "pg_quality_checks"):
            run_pipeline(name, settings)
    get_settings.cache_clear()
    with connect(settings) as conn:
        seed_incidents(conn)
        run_alerting(conn, monthly_budget_usd=100.0, webhook_url=None)
    return settings


@pytest.fixture(scope="session")
def client(pg: PostgresSettings):
    app = create_app(db=Database(pg), runner=JobRunner(enabled=True, settings=pg))
    # Deterministic settings regardless of the developer's .env (FastAPI dependency override).
    app.dependency_overrides[get_api_settings] = lambda: ApiSettings(
        monthly_budget_usd=100.0, alert_webhook_url=None
    )
    with TestClient(app) as test_client:  # `with` runs startup/shutdown (pool open/close)
        yield test_client


def sql(pg: PostgresSettings, query: str, *params):
    with connect(pg, autocommit=True) as conn:
        return conn.execute(query, params).fetchone()[0]
