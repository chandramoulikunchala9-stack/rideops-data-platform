"""Run pipelines/sql/quality/checks.sql and store the results in ops.data_quality_results."""

import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from rideops.config import SQL_DIR

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckResult:
    check_name: str
    entity: str
    check_type: str
    failed_rows: int
    total_rows: int
    threshold: float
    details: dict
    status: str


def decide_status(failed_rows: int, total_rows: int, threshold: float) -> str:
    """PASS: no failures. WARN: failure rate within threshold. FAIL: above threshold."""
    if failed_rows == 0:
        return "PASS"
    rate = failed_rows / total_rows if total_rows else 1.0
    return "WARN" if rate <= threshold else "FAIL"


def run_checks(
    conn: psycopg.Connection,
    run_id: str | None = None,
    sql_path: Path = SQL_DIR / "quality" / "checks.sql",
) -> list[CheckResult]:
    rows = conn.execute(sql_path.read_text(encoding="utf-8")).fetchall()
    results = []
    for name, entity, check_type, failed, total, threshold, details in rows:
        threshold = float(threshold) if isinstance(threshold, Decimal) else threshold
        results.append(
            CheckResult(
                name,
                entity,
                check_type,
                int(failed),
                int(total),
                threshold,
                details or {},
                decide_status(int(failed), int(total), threshold),
            )  # fmt: skip
        )
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO ops.data_quality_results (
                run_id, check_name, entity, check_type, status, failed_rows, total_rows,
                threshold, details
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (run_id, r.check_name, r.entity, r.check_type, r.status, r.failed_rows,
                 r.total_rows, r.threshold, Jsonb(r.details))
                for r in results
            ],
        )  # fmt: skip
    for r in results:
        log = logger.warning if r.status != "PASS" else logger.info
        log("DQ %-4s %-32s failed=%d/%d", r.status, r.check_name, r.failed_rows, r.total_rows)
    return results
