"""Monitoring: summary, alerts and incident history."""

from typing import Annotated

from fastapi import APIRouter, Depends

from rideops.config import ApiSettings
from rideops.monitoring import alerts as alert_rules
from rideops_api import schemas
from rideops_api.errors import ApiError
from rideops_api.params import DbDep

router = APIRouter(tags=["monitoring"])


def get_api_settings() -> ApiSettings:
    return ApiSettings()


SettingsDep = Annotated[ApiSettings, Depends(get_api_settings)]


@router.get("/monitoring/summary", response_model=schemas.MonitoringSummary)
def summary(db: DbDep) -> schemas.MonitoringSummary:
    runs_24h = db.fetch_all(
        "SELECT status, count(*) AS n FROM ops.pipeline_runs "
        "WHERE start_ts >= now() - interval '24 hours' GROUP BY status"
    )
    latest = db.fetch_all(
        """
        SELECT DISTINCT ON (pipeline_name) pipeline_name, status
        FROM ops.pipeline_runs ORDER BY pipeline_name, start_ts DESC
        """
    )
    by_status: dict[str, int] = {}
    for row in latest:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    long_running = db.scalar(
        """
        WITH baseline AS (
            SELECT pipeline_name, percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_sec) AS m
            FROM ops.pipeline_runs WHERE status = 'SUCCESS' GROUP BY pipeline_name
        )
        SELECT count(*) FROM ops.pipeline_runs r JOIN baseline b USING (pipeline_name)
        WHERE r.status = 'SUCCESS' AND r.duration_sec > 2 * b.m
          AND r.start_ts >= now() - interval '30 days'
        """
    )
    ingestion = db.fetch_one(
        """
        SELECT max(loaded_at) AS last_at,
               round((extract(epoch FROM now() - max(loaded_at)) / 3600)::numeric, 1) AS hours
        FROM ops.ingestion_log WHERE status = 'loaded'
        """
    )
    dq = db.fetch_all(
        """
        SELECT status, count(*) AS n FROM (
            SELECT DISTINCT ON (check_name) check_name, status
            FROM ops.data_quality_results ORDER BY check_name, checked_at DESC
        ) latest GROUP BY status
        """
    )
    active = db.fetch_all(
        "SELECT severity, count(*) AS n FROM ops.alerts WHERE NOT acknowledged GROUP BY severity"
    )
    return schemas.MonitoringSummary(
        runs_last_24h={r["status"]: r["n"] for r in runs_24h},
        pipelines_by_latest_status=by_status,
        failing_pipelines=[r["pipeline_name"] for r in latest if r["status"] == "FAILED"],
        long_running_runs_30d=long_running or 0,
        last_ingestion_at=ingestion["last_at"] if ingestion else None,
        hours_since_ingestion=float(ingestion["hours"])
        if ingestion and ingestion["hours"]
        else None,
        data_quality={r["status"]: r["n"] for r in dq},
        open_incidents=db.scalar("SELECT count(*) FROM ops.incidents WHERE status <> 'RESOLVED'"),
        active_alerts={r["severity"]: r["n"] for r in active},
    )


@router.get("/monitoring/alerts", response_model=list[schemas.Alert])
def list_alerts(db: DbDep, include_acknowledged: bool = False) -> list[schemas.Alert]:
    rows = db.fetch_all(
        """
        SELECT alert_id, rule, severity, subject, message, raised_at, acknowledged
        FROM ops.alerts WHERE %s OR NOT acknowledged
        ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END, raised_at DESC
        """,
        (include_acknowledged,),
    )
    return [schemas.Alert(**r) for r in rows]


@router.post("/monitoring/alerts/evaluate")
def evaluate_alerts(db: DbDep, settings: SettingsDep) -> dict:
    """Evaluate all alert rules now (also run on a schedule by `python -m rideops.monitoring alerts`)."""
    with db.pool.connection() as conn:
        return alert_rules.run_alerting(
            conn, settings.monthly_budget_usd, settings.alert_webhook_url
        )


@router.post("/monitoring/alerts/{alert_id}/acknowledge", response_model=schemas.Alert)
def acknowledge(alert_id: int, db: DbDep) -> schemas.Alert:
    row = db.fetch_one(
        """
        UPDATE ops.alerts SET acknowledged = true WHERE alert_id = %s
        RETURNING alert_id, rule, severity, subject, message, raised_at, acknowledged
        """,
        (alert_id,),
    )
    if row is None:
        raise ApiError(404, "not_found", f"Alert {alert_id} not found")
    return schemas.Alert(**row)


@router.get("/incidents", response_model=list[schemas.Incident])
def incidents(db: DbDep) -> list[schemas.Incident]:
    return [
        schemas.Incident(**r)
        for r in db.fetch_all("SELECT * FROM ops.incidents ORDER BY opened_at DESC")
    ]


@router.get("/incidents/{incident_id}", response_model=schemas.Incident)
def incident(incident_id: str, db: DbDep) -> schemas.Incident:
    row = db.fetch_one("SELECT * FROM ops.incidents WHERE incident_id = %s", (incident_id,))
    if row is None:
        raise ApiError(404, "not_found", f"Incident '{incident_id}' not found")
    return schemas.Incident(**row)
