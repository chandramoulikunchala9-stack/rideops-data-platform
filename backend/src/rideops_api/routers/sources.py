"""Source applications: where the data comes from, how much landed, and how much was rejected.

The transport company's data arrives from several applications. Each one delivers files for one
or more entities; the loader records every file in `ops.ingestion_log`, and rows that fail
validation land in `core.rejected_records`. This endpoint groups those facts per application.
"""

from fastapi import APIRouter

from rideops_api import schemas
from rideops_api.params import DateRangeDep, DbDep

router = APIRouter(tags=["sources"])

# Source application -> (display name, one-line description, entities it delivers).
SOURCE_APPS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("rider_app", "Rider app", "Ride bookings and rider accounts", ("bookings", "customers")),
    ("driver_app", "Driver app", "Driver profiles and their vehicles", ("drivers", "vehicles")),
    ("payments", "Payments gateway", "Charges, refunds and payment methods", ("payments",)),
    ("cloud_billing", "Cloud billing", "Daily cloud cost rows (simulated)", ("cloud_costs",)),
    ("platform_ops", "Platform operations", "Pipeline run history", ("pipeline_runs",)),
)

# Where each entity's validated rows live. A fixed whitelist: table names are never user input.
CURRENT_TABLE: dict[str, str] = {
    "bookings": "core.bookings",
    "customers": "core.customers",
    "drivers": "core.drivers",
    "vehicles": "core.vehicles",
    "payments": "core.payments",
    "cloud_costs": "ops.cloud_costs",
    "pipeline_runs": "ops.pipeline_runs",
}


@router.get("/sources", response_model=schemas.Sources)
def sources(db: DbDep) -> schemas.Sources:
    files = {
        r["entity"]: r
        for r in db.fetch_all(
            """
            SELECT entity,
                   count(*)                                     AS files,
                   count(*) FILTER (WHERE status = 'failed')    AS failed_files,
                   coalesce(sum(rows_loaded), 0)                AS rows_loaded,
                   coalesce(sum(file_bytes), 0)                 AS bytes,
                   max(loaded_at) FILTER (WHERE status = 'loaded') AS last_loaded_at
            FROM ops.ingestion_log GROUP BY entity
            """
        )
    }
    rejected = {
        r["entity"]: r["n"]
        for r in db.fetch_all("SELECT entity, count(*) AS n FROM core.rejected_records GROUP BY 1")
    }
    current = (
        db.fetch_one(
            "SELECT "
            + ", ".join(f"(SELECT count(*) FROM {t}) AS {e}" for e, t in CURRENT_TABLE.items())
        )
        or {}
    )

    apps: list[schemas.SourceApp] = []
    for app_id, name, description, entities in SOURCE_APPS:
        rows: list[schemas.SourceEntity] = []
        for entity in entities:
            f = files.get(entity, {})
            rows.append(
                schemas.SourceEntity(
                    entity=entity,
                    files=int(f.get("files", 0)),
                    failed_files=int(f.get("failed_files", 0)),
                    rows_loaded=int(f.get("rows_loaded", 0)),
                    rows_current=int(current.get(entity, 0)),
                    rows_rejected=int(rejected.get(entity, 0)),
                    bytes=int(f.get("bytes", 0)),
                    last_loaded_at=f.get("last_loaded_at"),
                )
            )
        loaded = sum(r.rows_loaded for r in rows)
        rejected_rows = sum(r.rows_rejected for r in rows)
        stamps = [r.last_loaded_at for r in rows if r.last_loaded_at]
        if any(r.failed_files for r in rows):
            status = "fail"
        elif loaded == 0:
            status = "empty"
        elif rejected_rows:
            status = "warn"
        else:
            status = "ok"
        apps.append(
            schemas.SourceApp(
                id=app_id,
                name=name,
                description=description,
                status=status,
                rows_loaded=loaded,
                rows_rejected=rejected_rows,
                last_loaded_at=max(stamps) if stamps else None,
                entities=rows,
            )
        )
    return schemas.Sources(sources=apps)


@router.get("/analytics/apps", response_model=schemas.AppFunnel)
def app_funnel(db: DbDep, period: DateRangeDep) -> schemas.AppFunnel:
    """Rider-app funnel by platform (android, ios, web), from the Spark lakehouse export.

    The table exists only after `python -m rideops.spark export` has run; until then the
    endpoint answers with `available: false` instead of an error.
    """
    meta = schemas.Period(start=period.start, end=period.end)
    if db.scalar("SELECT to_regclass('lakehouse.app_funnel_daily') IS NOT NULL") is not True:
        return schemas.AppFunnel(available=False, period=meta, platforms=[], daily=[])
    params = {"start": period.start, "end": period.end}
    platforms = db.fetch_all(
        """
        SELECT platform,
               sum(sessions)::int   AS sessions,
               sum(fare_views)::int AS fare_views,
               sum(bookings)::int   AS bookings,
               sum(crashes)::int    AS crashes,
               round(sum(bookings)::numeric / nullif(sum(sessions), 0), 4)::float AS conversion_rate
        FROM lakehouse.app_funnel_daily
        WHERE event_date BETWEEN %(start)s AND %(end)s
        GROUP BY platform ORDER BY sum(sessions) DESC
        """,
        params,
    )
    daily = db.fetch_all(
        """
        SELECT event_date AS date, platform, sessions::int AS sessions, bookings::int AS bookings
        FROM lakehouse.app_funnel_daily
        WHERE event_date BETWEEN %(start)s AND %(end)s
        ORDER BY event_date, platform
        """,
        params,
    )
    return schemas.AppFunnel(
        available=True,
        period=meta,
        platforms=[schemas.AppPlatformStats(**p) for p in platforms],
        daily=[schemas.AppDay(**d) for d in daily],
    )
