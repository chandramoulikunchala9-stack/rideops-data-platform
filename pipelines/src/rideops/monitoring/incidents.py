"""Incident history (monitoring/incidents/*.md) and a simulated incident for troubleshooting practice."""

import csv
import logging
from pathlib import Path

import psycopg
import yaml

from rideops.config import INCIDENTS_DIR

logger = logging.getLogger(__name__)

DRIFT_FILE = "landing/bookings/batch_date=2026-11-01/bookings_20261101_9001.csv"


def parse_incident(path: Path) -> dict:
    """Split YAML front matter from the Markdown body."""
    text = path.read_text(encoding="utf-8")
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front)
    meta["notes"] = body.strip()
    return meta


def seed_incidents(conn: psycopg.Connection, directory: Path = INCIDENTS_DIR) -> int:
    count = 0
    with conn.transaction(), conn.cursor() as cur:
        for path in sorted(directory.glob("INC-*.md")):
            m = parse_incident(path)
            run_id = m.get("run_id")
            if (
                run_id
                and not cur.execute(
                    "SELECT 1 FROM ops.pipeline_runs WHERE run_id = %s", (run_id,)
                ).fetchone()
            ):
                run_id = None  # history not loaded in this database
            cur.execute(
                """
                INSERT INTO ops.incidents (incident_id, title, severity, status, pipeline_name,
                    run_id, opened_at, resolved_at, root_cause, resolution, runbook)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (incident_id) DO UPDATE SET
                    title = excluded.title, severity = excluded.severity, status = excluded.status,
                    pipeline_name = excluded.pipeline_name, run_id = excluded.run_id,
                    opened_at = excluded.opened_at, resolved_at = excluded.resolved_at,
                    root_cause = excluded.root_cause, resolution = excluded.resolution,
                    runbook = excluded.runbook
                """,
                (m["incident_id"], m["title"], m["severity"], m["status"], m.get("pipeline_name"),
                 run_id, m["opened_at"], m.get("resolved_at"), m.get("source"), m["notes"],
                 m.get("runbook")),
            )  # fmt: skip
            count += 1
    return count


# ------------------------------------------------------------------ simulated incident


def simulate_schema_drift(data_dir: Path) -> Path:
    """Drop a bookings file whose columns were renamed by a 'source system release'.

    Then run `python -m rideops.db all`: the loader rejects the file (SchemaMismatchError),
    ops.ingestion_log records it as failed, the `landing_schema_mismatch` check FAILs and the
    `data_quality_failed` alert fires. Follow monitoring/runbooks/invalid-schema.md to resolve.
    """
    path = data_dir / DRIFT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["booking_ref", "customer", "fare_inr", "status", "requested_at"])
        writer.writerow(
            ["BKG-9900001", "CUS-000001", "180.50", "completed", "2026-11-01T08:00:00Z"]
        )
    logger.warning("Simulated incident: wrote schema-drifted file %s", path)
    return path


def resolve_schema_drift(conn: psycopg.Connection, data_dir: Path) -> None:
    """Runbook step: quarantine the bad file and clear its failed ingestion record."""
    path = data_dir / DRIFT_FILE
    if path.exists():
        target = data_dir / "quarantine_files" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
        logger.info("Moved %s to %s", path, target)
    with conn.transaction():
        conn.execute("DELETE FROM ops.ingestion_log WHERE file_path = %s", (DRIFT_FILE,))
