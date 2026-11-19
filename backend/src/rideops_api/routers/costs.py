"""Cloud usage and cost reporting. Every response states its data sources (simulated vs real)."""

from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from rideops_api import schemas
from rideops_api.errors import ApiError
from rideops_api.params import DbDep
from rideops_api.routers.monitoring import SettingsDep

router = APIRouter(prefix="/costs", tags=["costs"])

MonthParam = Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$", description="YYYY-MM")]


def _breakdown(db, column: str, month_start: date, month_end: date) -> list[schemas.CostBreakdown]:
    rows = db.fetch_all(
        f"""
        SELECT coalesce({column}, 'UNTAGGED') AS name, sum(cost_usd) AS cost_usd,
               sum(cost_usd) / nullif(sum(sum(cost_usd)) OVER (), 0) AS share
        FROM ops.cloud_costs
        WHERE usage_date BETWEEN %s AND %s
        GROUP BY 1 ORDER BY cost_usd DESC
        """,  # noqa: S608 - column is one of a fixed set chosen in code
        (month_start, month_end),
    )
    return [schemas.CostBreakdown(name=r["name"], cost_usd=float(r["cost_usd"]),
                                  share=round(float(r["share"] or 0), 4)) for r in rows]  # fmt: skip


@router.get("/summary", response_model=schemas.CostSummary)
def summary(db: DbDep, settings: SettingsDep, month: MonthParam = None) -> schemas.CostSummary:
    if month is None:
        latest = db.scalar("SELECT max(usage_date) FROM ops.cloud_costs")
        if latest is None:
            raise ApiError(404, "no_cost_data", "No cost data has been loaded")
        month = latest.strftime("%Y-%m")
    month_start = date.fromisoformat(f"{month}-01")
    month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    head = db.fetch_one(
        """
        SELECT sum(cost_usd) AS total, max(usage_date) AS as_of,
               coalesce(sum(cost_usd) FILTER (WHERE workload IS NULL), 0) AS untagged,
               array_agg(DISTINCT source) AS sources
        FROM ops.cloud_costs WHERE usage_date BETWEEN %s AND %s
        """,
        (month_start, month_end),
    )
    if head["total"] is None:
        raise ApiError(404, "no_cost_data", f"No cost data for {month}")
    total = float(head["total"])
    forecast = total / head["as_of"].day * month_end.day  # linear run-rate forecast
    budget = settings.monthly_budget_usd
    budget_status = (
        "OVER_BUDGET" if total > budget else ("FORECAST_OVER_BUDGET" if forecast > budget else "OK")
    )
    jobs = db.fetch_all(
        """
        WITH c AS (
            SELECT workload, sum(cost_usd) AS cost FROM ops.cloud_costs
            WHERE usage_date BETWEEN %(s)s AND %(e)s AND workload IS NOT NULL GROUP BY workload
        ),
        r AS (
            SELECT pipeline_name, count(*) AS runs FROM ops.pipeline_runs
            WHERE start_ts::date BETWEEN %(s)s AND %(e)s GROUP BY pipeline_name
        )
        SELECT c.workload AS pipeline_name, c.cost AS cost_usd, r.runs,
               c.cost / nullif(r.runs, 0) AS cost_per_run_usd
        FROM c JOIN r ON r.pipeline_name = c.workload
        ORDER BY c.cost DESC LIMIT 5
        """,
        {"s": month_start, "e": month_end},
    )
    return schemas.CostSummary(
        month=month,
        sources=sorted(head["sources"]),
        total_usd=round(total, 2),
        as_of=head["as_of"],
        forecast_usd=round(forecast, 2),
        budget_usd=budget,
        budget_status=budget_status,
        by_service=_breakdown(db, "service", month_start, month_end),
        by_workload=_breakdown(db, "workload", month_start, month_end),
        by_environment=_breakdown(db, "environment", month_start, month_end),
        untagged_usd=round(float(head["untagged"]), 2),
        expensive_jobs=[schemas.ExpensiveJob(**j) for j in jobs],
    )


@router.get("/trends", response_model=schemas.CostTrends)
def trends(
    db: DbDep,
    start_date: date | None = None,
    end_date: date | None = None,
    group_by: Literal["service", "workload", "environment", "provider"] = "service",
) -> schemas.CostTrends:
    end_date = end_date or db.scalar("SELECT max(usage_date) FROM ops.cloud_costs") or date.today()
    start_date = start_date or end_date - timedelta(days=89)
    if start_date > end_date:
        raise ApiError(400, "invalid_date_range", "start_date must be on or before end_date")
    params = {"s": start_date, "e": end_date}
    series = db.fetch_all(
        f"""
        SELECT usage_date AS date, coalesce({group_by}, 'UNTAGGED') AS "group",
               round(sum(cost_usd), 4) AS cost_usd
        FROM ops.cloud_costs WHERE usage_date BETWEEN %(s)s AND %(e)s
        GROUP BY 1, 2 ORDER BY 1, 2
        """,  # noqa: S608 - group_by is validated by the Literal type
        params,
    )
    daily = db.fetch_all(
        """
        SELECT usage_date AS date, 'total' AS "group", round(sum(cost_usd), 4) AS cost_usd
        FROM ops.cloud_costs WHERE usage_date BETWEEN %(s)s AND %(e)s GROUP BY 1 ORDER BY 1
        """,
        params,
    )
    anomalies = db.fetch_all(
        """
        WITH d AS (SELECT usage_date, sum(cost_usd) AS cost FROM ops.cloud_costs GROUP BY 1),
        s AS (
            SELECT usage_date, cost, avg(cost) OVER w AS mean, stddev_samp(cost) OVER w AS sd
            FROM d WINDOW w AS (ORDER BY usage_date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING)
        )
        SELECT usage_date AS date, round(cost, 2) AS cost_usd, round(mean, 2) AS trailing_mean_usd,
               round((cost - mean) / sd, 1) AS z_score
        FROM s WHERE sd > 0 AND cost > mean + 3 * sd AND usage_date BETWEEN %(s)s AND %(e)s
        ORDER BY usage_date
        """,
        params,
    )
    sources = db.scalar(
        "SELECT array_agg(DISTINCT source) FROM ops.cloud_costs WHERE usage_date BETWEEN %(s)s AND %(e)s",
        params,
    )
    return schemas.CostTrends(
        period=schemas.Period(start=start_date, end=end_date),
        group_by=group_by,
        sources=sorted(sources or []),
        series=[schemas.CostPoint(**r) for r in series],
        daily_totals=[schemas.CostPoint(**r) for r in daily],
        anomalies=[schemas.CostAnomaly(**r) for r in anomalies],
    )
