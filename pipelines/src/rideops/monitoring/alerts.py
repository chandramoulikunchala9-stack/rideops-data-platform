"""Alert rules: evaluated with SQL, stored in ops.alerts, optionally sent to a webhook.

Rules (each returns zero or more alerts, one per subject):
  failed_pipeline      latest run of a pipeline FAILED
  long_running         a RUNNING run is older than 3x its pipeline's median duration
  stale_ingestion      no landing file loaded for more than 26 hours
  data_quality_failed  the latest result of a data-quality check is FAIL
  budget               month-to-date spend or its forecast exceeds MONTHLY_BUDGET_USD
  cost_anomaly         a day's spend is > 3 standard deviations above the trailing 14 days

Alerts are de-duplicated on (rule, subject): re-evaluating does not create copies, and an alert
whose condition no longer holds is removed (auto-resolve). Only *new* alerts are sent to the
webhook, so on-call is not paged repeatedly for the same problem.
"""

import json
import logging
import urllib.request
from dataclasses import dataclass, field

import psycopg
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Alert:
    rule: str
    severity: str  # INFO | WARNING | CRITICAL
    subject: str
    message: str
    details: dict = field(default_factory=dict)


RULES: dict[str, str] = {
    "failed_pipeline": """
        SELECT * FROM (
            SELECT DISTINCT ON (pipeline_name) pipeline_name AS subject, run_id, status,
                   start_ts, error_message
            FROM ops.pipeline_runs ORDER BY pipeline_name, start_ts DESC
        ) latest WHERE status = 'FAILED'
    """,
    "long_running": """
        WITH baseline AS (
            SELECT pipeline_name,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_sec) AS median_sec
            FROM ops.pipeline_runs WHERE status = 'SUCCESS' GROUP BY pipeline_name
        )
        SELECT r.run_id AS subject, r.pipeline_name, b.median_sec,
               extract(epoch FROM now() - r.start_ts)::int AS running_sec
        FROM ops.pipeline_runs r JOIN baseline b USING (pipeline_name)
        WHERE r.status = 'RUNNING'
          AND extract(epoch FROM now() - r.start_ts) > 3 * greatest(b.median_sec, 60)
    """,
    "stale_ingestion": """
        SELECT 'landing' AS subject, max(loaded_at) AS last_loaded_at,
               round(extract(epoch FROM now() - max(loaded_at)) / 3600, 1) AS hours_since
        FROM ops.ingestion_log WHERE status = 'loaded'
        HAVING max(loaded_at) < now() - interval '26 hours'
    """,
    "data_quality_failed": """
        SELECT * FROM (
            SELECT DISTINCT ON (check_name) check_name AS subject, status, failed_rows,
                   total_rows, checked_at
            FROM ops.data_quality_results ORDER BY check_name, checked_at DESC
        ) latest WHERE status = 'FAIL'
    """,
    "budget": """
        WITH m AS (
            SELECT date_trunc('month', max(usage_date))::date AS month, max(usage_date) AS as_of
            FROM ops.cloud_costs
        )
        SELECT to_char(m.month, 'YYYY-MM') AS subject, m.as_of,
               sum(c.cost_usd) AS spend,
               sum(c.cost_usd) / extract(day FROM m.as_of)
                 * extract(day FROM (m.month + interval '1 month - 1 day')) AS forecast,
               array_agg(DISTINCT c.source) AS sources
        FROM ops.cloud_costs c CROSS JOIN m
        WHERE c.usage_date >= m.month
        GROUP BY m.month, m.as_of
    """,
    "cost_anomaly": """
        WITH daily AS (SELECT usage_date, sum(cost_usd) AS cost FROM ops.cloud_costs GROUP BY 1),
        stats AS (
            SELECT usage_date, cost, avg(cost) OVER w AS mean, stddev_samp(cost) OVER w AS sd
            FROM daily WINDOW w AS (ORDER BY usage_date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING)
        )
        SELECT usage_date::text AS subject, round(cost, 2) AS cost, round(mean, 2) AS mean,
               round((cost - mean) / nullif(sd, 0), 1) AS z_score
        FROM stats WHERE sd IS NOT NULL AND cost > mean + 3 * sd
    """,
}


def evaluate(conn: psycopg.Connection, monthly_budget_usd: float) -> list[Alert]:
    alerts: list[Alert] = []
    with conn.cursor(row_factory=dict_row) as cur:
        for row in cur.execute(RULES["failed_pipeline"]).fetchall():
            alerts.append(Alert("failed_pipeline", "CRITICAL", row["subject"],
                                f"Latest run {row['run_id']} failed: {(row['error_message'] or '')[:200]}",
                                {"run_id": row["run_id"]}))  # fmt: skip
        for row in cur.execute(RULES["long_running"]).fetchall():
            alerts.append(Alert("long_running", "WARNING", row["subject"],
                                f"{row['pipeline_name']} has been running {row['running_sec']}s "
                                f"(median {round(row['median_sec'])}s)", {}))  # fmt: skip
        for row in cur.execute(RULES["stale_ingestion"]).fetchall():
            alerts.append(Alert("stale_ingestion", "WARNING", "landing",
                                f"No new source files loaded for {row['hours_since']} hours",
                                {"last_loaded_at": row["last_loaded_at"].isoformat()}))  # fmt: skip
        for row in cur.execute(RULES["data_quality_failed"]).fetchall():
            alerts.append(Alert("data_quality_failed", "CRITICAL", row["subject"],
                                f"Check failed: {row['failed_rows']}/{row['total_rows']} rows",
                                {}))  # fmt: skip
        for row in cur.execute(RULES["budget"]).fetchall():
            label = "SIMULATED " if row["sources"] == ["simulated"] else ""
            if row["spend"] > monthly_budget_usd:
                alerts.append(Alert("budget", "CRITICAL", row["subject"],
                                    f"{label}month-to-date spend ${row['spend']:.2f} exceeds the "
                                    f"${monthly_budget_usd:.0f} budget", {"sources": row["sources"]}))  # fmt: skip
            elif row["forecast"] > monthly_budget_usd:
                alerts.append(Alert("budget", "WARNING", row["subject"],
                                    f"{label}forecast ${row['forecast']:.2f} will exceed the "
                                    f"${monthly_budget_usd:.0f} budget", {"sources": row["sources"]}))  # fmt: skip
        for row in cur.execute(RULES["cost_anomaly"]).fetchall():
            alerts.append(Alert("cost_anomaly", "WARNING", row["subject"],
                                f"Spend ${row['cost']} is {row['z_score']} standard deviations above "
                                f"the 14-day mean (${row['mean']})", {}))  # fmt: skip
    return alerts


def store(conn: psycopg.Connection, alerts: list[Alert]) -> list[Alert]:
    """Upsert current alerts, delete resolved ones. Returns the alerts that are new."""
    new: list[Alert] = []
    # Explicit tuple rows: callers may pass a connection configured with dict rows (the API pool).
    with conn.transaction(), conn.cursor(row_factory=tuple_row) as cur:
        existing = {(r[0], r[1]) for r in cur.execute("SELECT rule, subject FROM ops.alerts")}
        current = {(a.rule, a.subject) for a in alerts}
        for alert in alerts:
            if (alert.rule, alert.subject) not in existing:
                new.append(alert)
            cur.execute(
                """
                INSERT INTO ops.alerts (rule, severity, subject, message, details)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (rule, subject) DO UPDATE SET
                    severity = excluded.severity, message = excluded.message,
                    details = excluded.details
                """,
                (alert.rule, alert.severity, alert.subject, alert.message, Jsonb(alert.details)),
            )
        for rule, subject in existing - current:  # condition cleared -> auto-resolve
            cur.execute("DELETE FROM ops.alerts WHERE rule = %s AND subject = %s", (rule, subject))
    return new


def notify(alerts: list[Alert], webhook_url: str | None) -> int:
    """Send new alerts to a Slack/Teams-style incoming webhook. Returns how many were sent."""
    for alert in alerts:
        logger.warning(
            "ALERT %s %s [%s] %s", alert.severity, alert.rule, alert.subject, alert.message
        )
    if not webhook_url or not alerts:
        return 0
    text = "\n".join(f"*{a.severity}* `{a.rule}` {a.subject}: {a.message}" for a in alerts)
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5):  # noqa: S310 - URL comes from config
            return len(alerts)
    except OSError as exc:  # a broken webhook must not break monitoring
        logger.error("Alert webhook failed: %s", exc)
        return 0


def run_alerting(
    conn: psycopg.Connection, monthly_budget_usd: float, webhook_url: str | None
) -> dict:
    alerts = evaluate(conn, monthly_budget_usd)
    new = store(conn, alerts)
    sent = notify(new, webhook_url)
    return {"active": len(alerts), "new": len(new), "notified": sent}
