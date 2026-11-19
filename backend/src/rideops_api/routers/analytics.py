"""Business analytics endpoints. All figures are read from the analytics (gold) tables."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from rideops_api import schemas
from rideops_api.params import DateRangeDep, DbDep, PaginationDep

router = APIRouter(prefix="/analytics", tags=["analytics"])

CityParam = Annotated[str | None, Query(pattern="^[A-Z]{3}$", description="City code, e.g. BLR")]


@router.get("/overview", response_model=schemas.Overview)
def overview(db: DbDep, period: DateRangeDep) -> schemas.Overview:
    params = {"start": period.start, "end": period.end}
    totals = db.fetch_one(
        """
        SELECT coalesce(sum(total_bookings), 0)     AS total_bookings,
               coalesce(sum(completed_rides), 0)    AS completed_rides,
               coalesce(sum(gross_revenue), 0)      AS revenue,
               coalesce(sum(cancelled_bookings), 0) AS cancelled
        FROM analytics.daily_city_summary
        WHERE booking_date BETWEEN %(start)s AND %(end)s
        """,
        params,
    )
    people = db.fetch_one(
        """
        SELECT count(DISTINCT driver_id) FILTER (WHERE status = 'completed') AS drivers,
               count(DISTINCT customer_id)                                   AS customers
        FROM core.bookings
        WHERE booking_date BETWEEN %(start)s AND %(end)s
        """,
        params,
    )
    success_rate = db.scalar(
        """
        SELECT round(count(*) FILTER (WHERE status = 'SUCCESS')::numeric
                     / nullif(count(*) FILTER (WHERE status <> 'RUNNING'), 0), 4)
        FROM ops.pipeline_runs WHERE start_ts >= now() - interval '30 days'
        """
    )
    failures = db.fetch_all(
        """
        SELECT run_id, pipeline_name, status, start_ts, error_message
        FROM ops.pipeline_runs WHERE status = 'FAILED' ORDER BY start_ts DESC LIMIT 5
        """
    )
    spend = db.fetch_one(
        """
        WITH m AS (SELECT date_trunc('month', max(usage_date))::date AS month FROM ops.cloud_costs)
        SELECT to_char(m.month, 'YYYY-MM') AS month, coalesce(sum(c.cost_usd), 0) AS amount,
               coalesce(array_agg(DISTINCT c.source) FILTER (WHERE c.source IS NOT NULL), '{}') AS sources
        FROM m LEFT JOIN ops.cloud_costs c ON c.usage_date >= m.month
        GROUP BY m.month
        """
    )
    total = int(totals["total_bookings"])
    return schemas.Overview(
        period=schemas.Period(start=period.start, end=period.end),
        data_as_of=db.scalar("SELECT max(booking_date) FROM analytics.daily_city_summary"),
        total_bookings=total,
        completed_rides=int(totals["completed_rides"]),
        total_revenue_inr=float(totals["revenue"]),
        cancellation_rate=round(int(totals["cancelled"]) / total, 4) if total else 0.0,
        active_drivers=people["drivers"],
        active_customers=people["customers"],
        pipeline_success_rate=float(success_rate) if success_rate is not None else None,
        failed_runs_30d=db.scalar(
            "SELECT count(*) FROM ops.pipeline_runs "
            "WHERE status = 'FAILED' AND start_ts >= now() - interval '30 days'"
        ),
        recent_failures=[schemas.RunBrief(**f) for f in failures],
        cloud_spend=schemas.CloudSpend(
            month=spend["month"] if spend else None,
            amount_usd=float(spend["amount"]) if spend else 0.0,
            sources=list(spend["sources"]) if spend else [],
        ),
    )


@router.get("/revenue", response_model=schemas.Revenue)
def revenue(
    db: DbDep,
    period: DateRangeDep,
    city: CityParam = None,
    granularity: Literal["day", "week", "month"] = "day",
) -> schemas.Revenue:
    params = {"start": period.start, "end": period.end, "city": city, "grain": granularity}
    series = db.fetch_all(
        """
        SELECT date_trunc(%(grain)s, booking_date)::date AS period, city,
               sum(gross_revenue) AS revenue_inr, sum(total_bookings) AS bookings
        FROM analytics.daily_city_summary
        WHERE booking_date BETWEEN %(start)s AND %(end)s
          AND (%(city)s::text IS NULL OR city = %(city)s)
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
        params,
    )
    by_city = db.fetch_all(
        """
        SELECT s.city, c.city_name, sum(s.gross_revenue) AS revenue_inr,
               sum(s.total_bookings) AS bookings,
               round(sum(s.gross_revenue) / nullif(sum(s.total_bookings), 0), 2) AS avg_fare_inr
        FROM analytics.daily_city_summary s JOIN core.cities c ON c.city_code = s.city
        WHERE s.booking_date BETWEEN %(start)s AND %(end)s
          AND (%(city)s::text IS NULL OR s.city = %(city)s)
        GROUP BY s.city, c.city_name ORDER BY revenue_inr DESC
        """,
        params,
    )
    return schemas.Revenue(
        period=schemas.Period(start=period.start, end=period.end),
        granularity=granularity,
        series=[schemas.RevenuePoint(**r) for r in series],
        by_city=[schemas.CityRevenue(**r) for r in by_city],
    )


_DAILY_SQL = """
    SELECT booking_date AS date, sum(total_bookings) AS total, sum(completed_rides) AS completed,
           sum(cancelled_bookings) AS cancelled, sum(no_driver_bookings) AS no_driver_found,
           round(sum(cancelled_bookings)::numeric / nullif(sum(total_bookings), 0), 4) AS cancellation_rate
    FROM analytics.daily_city_summary
    WHERE booking_date BETWEEN %(start)s AND %(end)s
      AND (%(city)s::text IS NULL OR city = %(city)s)
    GROUP BY booking_date ORDER BY booking_date
"""


@router.get("/bookings", response_model=schemas.Bookings)
def bookings(db: DbDep, period: DateRangeDep, city: CityParam = None) -> schemas.Bookings:
    rows = db.fetch_all(_DAILY_SQL, {"start": period.start, "end": period.end, "city": city})
    return schemas.Bookings(
        period=schemas.Period(start=period.start, end=period.end),
        daily=[schemas.DailyBookings(**r) for r in rows],
    )


@router.get("/drivers", response_model=schemas.Page[schemas.Driver])
def drivers(
    db: DbDep,
    page: PaginationDep,
    city: CityParam = None,
    sort: Literal["revenue", "completion_rate", "rating", "rides"] = "revenue",
) -> schemas.Page[schemas.Driver]:
    # Whitelisted ORDER BY: user input never becomes SQL text.
    order = {
        "revenue": "p.total_revenue DESC",
        "completion_rate": "p.completion_rate DESC, p.completed_rides DESC",
        "rating": "p.rating DESC NULLS LAST",
        "rides": "p.completed_rides DESC",
    }[sort]
    params = {"city": city, "limit": page.limit, "offset": page.offset}
    rows = db.fetch_all(
        f"""
        SELECT p.driver_id, d.full_name, p.city, p.rating, p.total_assigned, p.completed_rides,
               p.driver_cancellations, p.completion_rate, p.total_revenue, p.active_days,
               p.revenue_rank_in_city
        FROM analytics.driver_performance p
        LEFT JOIN core.drivers d ON d.driver_id = p.driver_id
        WHERE (%(city)s::text IS NULL OR p.city = %(city)s)
        ORDER BY {order}, p.driver_id
        LIMIT %(limit)s OFFSET %(offset)s
        """,  # noqa: S608 - `order` comes from the whitelist above
        params,
    )
    total = db.scalar(
        "SELECT count(*) FROM analytics.driver_performance WHERE (%(city)s::text IS NULL OR city = %(city)s)",
        params,
    )
    return schemas.Page[schemas.Driver](
        items=[schemas.Driver(**r) for r in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/cancellations", response_model=schemas.Cancellations)
def cancellations(db: DbDep, period: DateRangeDep, city: CityParam = None) -> schemas.Cancellations:
    params = {"start": period.start, "end": period.end, "city": city}
    reasons = db.fetch_all(
        """
        SELECT cancelled_by, cancellation_reason, sum(cancellations) AS cancellations,
               sum(fees_collected) AS fees_collected
        FROM analytics.cancellation_summary
        WHERE booking_date BETWEEN %(start)s AND %(end)s
          AND (%(city)s::text IS NULL OR city = %(city)s)
        GROUP BY 1, 2 ORDER BY cancellations DESC
        """,
        params,
    )
    return schemas.Cancellations(
        period=schemas.Period(start=period.start, end=period.end),
        by_reason=[schemas.CancellationReason(**r) for r in reasons],
        daily_rate=[schemas.DailyBookings(**r) for r in db.fetch_all(_DAILY_SQL, params)],
    )


@router.get("/payments", response_model=schemas.Payments)
def payments(db: DbDep, period: DateRangeDep) -> schemas.Payments:
    rows = db.fetch_all(
        """
        SELECT method, sum(attempts) AS attempts, sum(failed_attempts) AS failed_attempts,
               round(sum(failed_attempts)::numeric / nullif(sum(attempts), 0), 4) AS failure_rate,
               sum(charged_amount) AS charged_amount, sum(refunded_amount) AS refunded_amount
        FROM analytics.payment_summary
        WHERE payment_date BETWEEN %(start)s AND %(end)s
        GROUP BY method ORDER BY attempts DESC
        """,
        {"start": period.start, "end": period.end},
    )
    return schemas.Payments(
        period=schemas.Period(start=period.start, end=period.end),
        by_method=[schemas.PaymentMethodStats(**r) for r in rows],
    )
