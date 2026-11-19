"""Reusable query-parameter dependencies (date ranges, pagination)."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated

from fastapi import Depends, Query

from rideops_api.db import Database, get_db
from rideops_api.errors import ApiError


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date


def date_range(
    db: Annotated[Database, Depends(get_db)],
    start_date: Annotated[date | None, Query(description="Inclusive, YYYY-MM-DD")] = None,
    end_date: Annotated[date | None, Query(description="Inclusive, YYYY-MM-DD")] = None,
) -> DateRange:
    """Defaults to the last 30 days *of available data* (the data is historical/synthetic)."""
    if end_date is None:
        end_date = (
            db.scalar("SELECT max(booking_date) FROM analytics.daily_city_summary") or date.today()
        )
    if start_date is None:
        start_date = end_date - timedelta(days=29)
    if start_date > end_date:
        raise ApiError(400, "invalid_date_range", "start_date must be on or before end_date")
    if (end_date - start_date).days > 366:
        raise ApiError(400, "invalid_date_range", "date range cannot exceed 366 days")
    return DateRange(start_date, end_date)


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def pagination(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    return Pagination(limit, offset)


DateRangeDep = Annotated[DateRange, Depends(date_range)]
PaginationDep = Annotated[Pagination, Depends(pagination)]
DbDep = Annotated[Database, Depends(get_db)]
