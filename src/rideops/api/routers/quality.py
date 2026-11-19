"""Data-quality results and quarantined records."""

from fastapi import APIRouter

from rideops.api import schemas
from rideops.api.params import DbDep

router = APIRouter(tags=["data quality"])


@router.get("/data-quality", response_model=schemas.DataQuality)
def data_quality(db: DbDep) -> schemas.DataQuality:
    checks = db.fetch_all(
        """
        SELECT DISTINCT ON (check_name)
               check_name, entity, check_type, status, failed_rows, total_rows, failure_rate,
               threshold, details, checked_at
        FROM ops.data_quality_results
        ORDER BY check_name, checked_at DESC
        """
    )
    checks.sort(key=lambda c: ({"FAIL": 0, "WARN": 1, "PASS": 2}[c["status"]], c["check_name"]))
    counts: dict[str, int] = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for c in checks:
        counts[c["status"]] += 1
    rejections = db.fetch_all(
        """
        SELECT entity, reason, count(*) AS rows FROM core.rejected_records
        GROUP BY entity, reason ORDER BY rows DESC
        """
    )
    return schemas.DataQuality(
        status_counts=counts,
        checks=[schemas.CheckResult(**c) for c in checks],
        rejections=[schemas.Rejection(**r) for r in rejections],
        last_checked_at=max((c["checked_at"] for c in checks), default=None),
    )


@router.get("/data-quality/{check_name}/history", response_model=list[schemas.CheckResult])
def check_history(check_name: str, db: DbDep, limit: int = 30) -> list[schemas.CheckResult]:
    rows = db.fetch_all(
        """
        SELECT check_name, entity, check_type, status, failed_rows, total_rows, failure_rate,
               threshold, details, checked_at
        FROM ops.data_quality_results WHERE check_name = %s
        ORDER BY checked_at DESC LIMIT %s
        """,
        (check_name, min(max(limit, 1), 200)),
    )
    return [schemas.CheckResult(**r) for r in rows]
