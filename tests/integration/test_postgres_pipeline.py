"""End-to-end PostgreSQL pipeline: generate -> load -> transform -> analytics -> quality.

Runs against a throwaway database `rideops_test` (dropped and recreated per session) on the
server configured by POSTGRES_* variables. Skipped automatically if PostgreSQL is unreachable.
Run only these tests with:  pytest -m postgres
"""

import json
from pathlib import Path

import psycopg
import pytest

from rideops.config import PostgresSettings
from rideops.db.connection import connect
from rideops.db.migrate import apply_migrations, seed_reference_data
from rideops.db.queries import load_queries
from rideops.db.transform import build_analytics, transform_core
from rideops.generators.__main__ import main as generate
from rideops.ingestion.postgres_loader import load_landing
from rideops.monitoring.tracker import RunTracker
from rideops.quality.checks import run_checks

pytestmark = pytest.mark.postgres
TEST_DB = "rideops_test"


@pytest.fixture(scope="module")
def pg() -> PostgresSettings:
    try:
        base = PostgresSettings()
        with psycopg.connect(base.conninfo(), autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
            admin.execute(f"CREATE DATABASE {TEST_DB}")
    except (psycopg.OperationalError, ValueError) as exc:  # ValueError: password not configured
        pytest.skip(f"PostgreSQL not available: {exc}")
    return base.model_copy(update={"db": TEST_DB})


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("data")
    assert generate(["--scale", "small", "--seed", "42", "--data-dir", str(path)]) == 0
    return path


@pytest.fixture(scope="module")
def loaded(pg: PostgresSettings, data_dir: Path) -> PostgresSettings:
    with connect(pg) as conn:
        apply_migrations(conn)
        seed_reference_data(conn)
        summary = load_landing(conn, data_dir)
        assert summary.failed == 0 and summary.loaded == 7
        transform_core(conn)
        build_analytics(conn)
    return pg


def _scalar(pg: PostgresSettings, sql: str, *params):
    with connect(pg, autocommit=True) as conn:
        return conn.execute(sql, params).fetchone()[0]


def test_every_distinct_booking_is_either_in_core_or_rejected(loaded, data_dir) -> None:
    distinct_ids = _scalar(loaded, "SELECT count(DISTINCT booking_id) FROM raw.bookings")
    in_core = _scalar(loaded, "SELECT count(*) FROM core.bookings")
    rejected = _scalar(
        loaded, "SELECT count(*) FROM core.rejected_records WHERE entity = 'bookings'"
    )
    assert distinct_ids == 10_000
    assert in_core + rejected == distinct_ids  # nothing silently lost


def test_raw_row_counts_match_the_generation_manifest(loaded, data_dir) -> None:
    manifest = json.loads(
        (data_dir / "landing" / "_manifests" / "generation_0001.json").read_text(encoding="utf-8")
    )
    for entity in ("customers", "drivers", "vehicles", "bookings", "payments", "cloud_costs"):
        assert (
            _scalar(loaded, f"SELECT count(*) FROM raw.{entity}")
            == (manifest["entities"][entity]["rows"])
        )


def test_latest_version_wins_for_duplicate_deliveries(loaded) -> None:
    mismatches = _scalar(
        loaded,
        """
        SELECT count(*) FROM core.customers c
        JOIN (SELECT customer_id, max(core.parse_ts(updated_at)) AS latest
              FROM raw.customers GROUP BY customer_id) r USING (customer_id)
        WHERE c.updated_at <> r.latest
        """,
    )
    assert mismatches == 0


def test_injected_invalid_values_never_reach_core(loaded) -> None:
    assert _scalar(loaded, "SELECT count(*) FROM core.bookings WHERE fare_amount < 0") == 0
    assert _scalar(loaded, "SELECT count(*) FROM core.payments WHERE currency <> 'INR'") == 0
    assert _scalar(loaded, "SELECT count(*) FROM core.drivers WHERE rating > 5") == 0
    assert _scalar(loaded, "SELECT count(*) FROM core.bookings WHERE status <> lower(status)") == 0
    # repaired, not dropped: legacy timestamps were standardised and flagged
    assert (
        _scalar(
            loaded,
            "SELECT count(*) FROM core.bookings"
            " WHERE 'legacy_timestamp_standardised' = ANY(dq_flags)",
        )
        > 0
    )


def test_rerunning_load_and_transform_is_a_no_op(loaded, data_dir) -> None:
    before = _scalar(loaded, "SELECT count(*) FROM core.bookings")
    with connect(loaded) as conn:
        summary = load_landing(conn, data_dir)
        counts = transform_core(conn)
    assert (summary.loaded, summary.skipped) == (0, 7)
    assert counts == {"read": 0, "written": 0, "rejected": 0}
    assert _scalar(loaded, "SELECT count(*) FROM core.bookings") == before


def test_schema_mismatch_file_is_recorded_and_skipped(loaded, tmp_path) -> None:
    bad = (
        tmp_path / "landing" / "customers" / "batch_date=2026-11-01" / "customers_20261101_0002.csv"
    )
    bad.parent.mkdir(parents=True)
    bad.write_text("customer_id,full_name,favourite_colour\nCUS-999998,X,blue\n", encoding="utf-8")
    with connect(loaded) as conn:
        summary = load_landing(conn, tmp_path)
        results = {r.check_name: r for r in run_checks(conn)}
    assert summary.failed == 1 and summary.loaded == 0
    status = _scalar(
        loaded, "SELECT status FROM ops.ingestion_log WHERE file_path LIKE %s", "%20261101_0002%"
    )
    assert status == "failed"
    assert results["landing_schema_mismatch"].status == "FAIL"


def test_analytics_tables_are_built_and_consistent(loaded) -> None:
    gold_total = _scalar(loaded, "SELECT sum(total_bookings) FROM analytics.daily_city_summary")
    assert gold_total == _scalar(loaded, "SELECT count(*) FROM core.bookings")
    gold_revenue = _scalar(loaded, "SELECT sum(gross_revenue) FROM analytics.daily_city_summary")
    assert gold_revenue == _scalar(loaded, "SELECT sum(fare_amount) FROM core.bookings")


def test_every_named_analytics_query_executes(loaded) -> None:
    with connect(loaded, autocommit=True) as conn:
        for name, sql in load_queries().items():
            try:
                conn.execute(sql).fetchall()
            except psycopg.Error as exc:  # pragma: no cover - message for the failure report
                pytest.fail(f"query {name} failed: {exc}")


def test_run_tracker_records_success_and_failure(loaded) -> None:
    with RunTracker("pg_test_ok", layer="silver", settings=loaded) as ok:
        ok.add_counts(read=10, written=9, rejected=1)
    with (
        pytest.raises(RuntimeError),
        RunTracker("pg_test_fail", layer="silver", settings=loaded) as bad,
    ):
        raise RuntimeError("disk full")

    with connect(loaded, autocommit=True) as conn:
        row = conn.execute(
            "SELECT status, records_written, records_rejected"
            " FROM ops.pipeline_runs WHERE run_id = %s",
            (ok.run_id,),
        ).fetchone()
        assert row == ("SUCCESS", 9, 1)
        status, error = conn.execute(
            "SELECT status, error_message FROM ops.pipeline_runs WHERE run_id = %s", (bad.run_id,)
        ).fetchone()
        assert status == "FAILED" and error == "RuntimeError: disk full"
        logs = conn.execute(
            "SELECT level FROM ops.pipeline_run_logs WHERE run_id = %s ORDER BY log_id",
            (bad.run_id,),
        ).fetchall()
        assert ("ERROR",) in logs
