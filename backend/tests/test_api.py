"""REST API tests: status codes, response shapes, and figures reconciled against SQL."""

import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from rideops.config import PostgresSettings
from rideops_api.db import Database
from rideops_api.main import create_app
from rideops_api.runner import JobRunner

from .conftest import sql

V1 = "/api/v1"


# ------------------------------------------------------------------ health & errors


def test_health_ok(client) -> None:
    body = client.get(f"{V1}/health").json()
    assert body["status"] == "ok" and body["database"] == "ok"


def test_health_returns_503_when_database_is_down(pg) -> None:
    unreachable = pg.model_copy(update={"port": 1})
    app = create_app(db=Database(unreachable, timeout=1), runner=JobRunner(enabled=False))
    with TestClient(app) as c:
        response = c.get(f"{V1}/health")
        assert response.status_code == 503
        assert response.json()["database"] == "unavailable"
        error = c.get(f"{V1}/incidents")
        assert error.status_code == 503
        assert error.json()["error"]["code"] == "database_unavailable"


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/analytics/revenue?city=bangalore", 422, "validation_error"),
        ("/analytics/drivers?limit=0", 422, "validation_error"),
        (
            "/analytics/overview?start_date=2026-10-30&end_date=2026-10-01",
            400,
            "invalid_date_range",
        ),
        ("/pipelines/does_not_exist", 404, "not_found"),
        ("/runs/RUN-missing", 404, "not_found"),
        ("/incidents/INC-0000-000", 404, "not_found"),
        ("/costs/summary?month=2001-01", 404, "no_cost_data"),
    ],
)
def test_errors_use_one_json_shape(client, path: str, status: int, code: str) -> None:
    response = client.get(V1 + path)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_openapi_documents_all_required_endpoints(client) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    for required in ("/api/v1/health", "/api/v1/analytics/overview", "/api/v1/analytics/revenue",
                     "/api/v1/analytics/bookings", "/api/v1/analytics/drivers", "/api/v1/pipelines",
                     "/api/v1/pipelines/{name}", "/api/v1/pipelines/{name}/logs",
                     "/api/v1/pipelines/{name}/retry", "/api/v1/monitoring/summary",
                     "/api/v1/costs/summary", "/api/v1/costs/trends", "/api/v1/data-quality"):  # fmt: skip
        assert required in paths, required


# ------------------------------------------------------------------ analytics


def test_overview_reconciles_with_core_tables(client, pg) -> None:
    body = client.get(f"{V1}/analytics/overview").json()
    start, end = body["period"]["start"], body["period"]["end"]
    expected = sql(
        pg, "SELECT count(*) FROM core.bookings WHERE booking_date BETWEEN %s AND %s", start, end
    )
    revenue = sql(
        pg,
        "SELECT sum(fare_amount) FROM core.bookings WHERE booking_date BETWEEN %s AND %s",
        start,
        end,
    )
    assert body["total_bookings"] == expected
    assert Decimal(str(body["total_revenue_inr"])) == revenue
    assert body["cloud_spend"]["sources"] == ["simulated"]
    assert 0 <= body["cancellation_rate"] < 1


def test_revenue_by_city_adds_up_to_total(client) -> None:
    overview = client.get(f"{V1}/analytics/overview").json()
    revenue = client.get(f"{V1}/analytics/revenue").json()
    assert round(sum(c["revenue_inr"] for c in revenue["by_city"]), 2) == round(
        overview["total_revenue_inr"], 2
    )
    city = client.get(f"{V1}/analytics/revenue?city=BLR&granularity=week").json()
    assert {p["city"] for p in city["series"]} == {"BLR"}


def test_drivers_are_paginated_and_sorted(client) -> None:
    first = client.get(f"{V1}/analytics/drivers?limit=5&offset=0").json()
    second = client.get(f"{V1}/analytics/drivers?limit=5&offset=5").json()
    assert first["total"] == second["total"] > 10
    revenues = [d["total_revenue"] for d in first["items"] + second["items"]]
    assert revenues == sorted(revenues, reverse=True)
    assert not {d["driver_id"] for d in first["items"]} & {d["driver_id"] for d in second["items"]}


def test_bookings_cancellations_and_payments(client) -> None:
    daily = client.get(f"{V1}/analytics/bookings").json()["daily"]
    assert all(d["completed"] + d["cancelled"] + d["no_driver_found"] <= d["total"] for d in daily)
    assert client.get(f"{V1}/analytics/cancellations").json()["by_reason"]
    methods = {m["method"] for m in client.get(f"{V1}/analytics/payments").json()["by_method"]}
    assert {"upi", "card", "cash"} <= methods


# ------------------------------------------------------------------ pipelines


def test_pipeline_list_marks_local_pipelines_retryable(client) -> None:
    pipelines = {p["pipeline_name"]: p for p in client.get(f"{V1}/pipelines").json()}
    assert pipelines["pg_transform_core"]["retryable"] is True
    assert pipelines["ingest_payments"]["retryable"] is False
    assert "simulated_history" in pipelines["ingest_payments"]["run_sources"]


def test_pipeline_history_filters_by_status(client) -> None:
    page = client.get(f"{V1}/pipelines/ingest_payments?status=FAILED&limit=50").json()
    assert page["items"] and all(r["status"] == "FAILED" for r in page["items"])


def test_logs_for_real_and_simulated_runs(client) -> None:
    real = client.get(f"{V1}/pipelines/pg_transform_core/logs").json()
    assert any("Run started" in line["message"] for line in real["logs"])
    simulated = client.get(f"{V1}/pipelines/ingest_bookings/logs").json()
    assert simulated["logs"] == [] and "Simulated" in simulated["note"]


def test_run_detail_links_the_incident(client) -> None:
    runs = client.get(f"{V1}/pipelines/ingest_payments?status=FAILED&limit=200").json()["items"]
    incident_run = next(r for r in runs if "DELTA_FAILED_TO_MERGE" in (r["error_message"] or ""))
    detail = client.get(f"{V1}/runs/{incident_run['run_id']}").json()
    assert [i["incident_id"] for i in detail["incidents"]] == ["INC-2026-001"]


# ------------------------------------------------------------------ retry


def test_retry_rules(client) -> None:
    assert client.post(f"{V1}/pipelines/does_not_exist/retry").status_code == 404
    response = client.post(f"{V1}/pipelines/ingest_payments/retry")
    assert response.status_code == 400 and response.json()["error"]["code"] == "not_retryable"


def test_retry_is_refused_when_runner_disabled(pg) -> None:
    app = create_app(db=Database(pg), runner=JobRunner(enabled=False))
    with TestClient(app) as c:
        response = c.post(f"{V1}/pipelines/pg_quality_checks/retry")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "runner_disabled"


def test_retry_really_runs_the_pipeline(client, pg) -> None:
    before = sql(
        pg, "SELECT count(*) FROM ops.pipeline_runs WHERE pipeline_name = 'pg_quality_checks'"
    )
    response = client.post(f"{V1}/pipelines/pg_quality_checks/retry")
    assert response.status_code == 202
    retry_of = response.json()["retry_of_run_id"]

    deadline = time.time() + 60
    while time.time() < deadline:
        done = sql(pg, "SELECT count(*) FROM ops.pipeline_runs WHERE pipeline_name = 'pg_quality_checks' "
                       "AND trigger = 'retry' AND status <> 'RUNNING' AND retry_of_run_id = %s", retry_of)  # fmt: skip
        if done:
            break
        time.sleep(0.5)
    assert done == 1
    after = sql(
        pg, "SELECT count(*) FROM ops.pipeline_runs WHERE pipeline_name = 'pg_quality_checks'"
    )
    assert after == before + 1


# ------------------------------------------------------------------ monitoring, costs, DQ, access


def test_monitoring_summary_and_alert_lifecycle(client) -> None:
    summary = client.get(f"{V1}/monitoring/summary").json()
    assert summary["data_quality"] and summary["pipelines_by_latest_status"]

    assert client.post(f"{V1}/monitoring/alerts/evaluate").status_code == 200
    alerts = client.get(f"{V1}/monitoring/alerts").json()
    budget = next(a for a in alerts if a["rule"] == "budget")  # fixture budget is only $100
    acked = client.post(f"{V1}/monitoring/alerts/{budget['alert_id']}/acknowledge").json()
    assert acked["acknowledged"] is True
    assert budget["alert_id"] not in {
        a["alert_id"] for a in client.get(f"{V1}/monitoring/alerts").json()
    }
    assert client.post(f"{V1}/monitoring/alerts/999999/acknowledge").status_code == 404


def test_incident_history(client) -> None:
    ids = {i["incident_id"] for i in client.get(f"{V1}/incidents").json()}
    assert {"INC-2026-001", "INC-2026-002", "INC-2026-003", "INC-2026-004"} <= ids
    incident = client.get(f"{V1}/incidents/INC-2026-001").json()
    assert "Root cause" in incident["resolution"]


def test_cost_summary_is_labelled_and_reconciles(client, pg) -> None:
    body = client.get(f"{V1}/costs/summary").json()
    assert body["sources"] == ["simulated"]
    month_total = sql(pg, "SELECT sum(cost_usd) FROM ops.cloud_costs WHERE to_char(usage_date, 'YYYY-MM') = %s",
                      body["month"])  # fmt: skip
    assert body["total_usd"] == pytest.approx(float(month_total), abs=0.01)
    assert sum(s["cost_usd"] for s in body["by_service"]) == pytest.approx(
        body["total_usd"], abs=0.01
    )
    assert body["budget_status"] in {"OK", "FORECAST_OVER_BUDGET", "OVER_BUDGET"}


def test_cost_trends(client) -> None:
    body = client.get(f"{V1}/costs/trends?group_by=environment").json()
    assert {p["group"] for p in body["series"]} <= {"prod", "dev"}
    assert body["daily_totals"]
    assert client.get(f"{V1}/costs/trends?group_by=colour").status_code == 422


def test_data_quality_endpoint(client) -> None:
    body = client.get(f"{V1}/data-quality").json()
    assert sum(body["status_counts"].values()) == len(body["checks"]) >= 15
    assert body["rejections"] and all(r["rows"] > 0 for r in body["rejections"])


def test_access_settings_never_expose_the_password(client) -> None:
    body = client.get(f"{V1}/settings/access").json()
    assert [r["name"] for r in body["roles"]] == [
        "Platform Administrator", "Data Engineer", "Data Analyst", "Read-only User"
    ]  # fmt: skip
    password = PostgresSettings().password.get_secret_value()
    assert password not in client.get(f"{V1}/settings/access").text
