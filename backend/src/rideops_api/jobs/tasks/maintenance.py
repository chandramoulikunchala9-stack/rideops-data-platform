"""System jobs: retention (§12) and a no-op used by tests and smoke checks."""

import logging

from rideops_api.jobs.registry import JobContext, job

logger = logging.getLogger(__name__)


@job("noop", timeout_s=5, max_attempts=1, priority=0)
def noop(ctx: JobContext) -> None:
    if ctx.payload.get("fail"):
        raise RuntimeError("requested failure")


@job("retention", timeout_s=300, max_attempts=3, priority=10)
def retention(ctx: JobContext) -> None:
    """Delete expired data (§12 retention): auth rows, finished jobs, raw snapshots (after they are
    rolled up), roll-ups, cached query results, old tests/syncs, and connections past the 7-day
    restore window (their snapshots go with them, FR-17)."""
    from rideops_api.lens.status import rollups
    from rideops_api.settings import get_lens_settings

    s = ctx.settings or get_lens_settings()
    rollups.rollup(ctx.db, s)  # never drop raw rows that aren't in an hourly roll-up yet
    raw, hourly, daily = (
        int(s.retention_raw_days),
        int(s.retention_hourly_days),
        int(s.retention_daily_days),
    )
    statements = {
        "status_snapshots": "DELETE FROM app.status_snapshots "
        f"WHERE taken_at < date_trunc('hour', now() - interval '{raw} days')",
        "rollups_hourly": "DELETE FROM app.metric_rollups "
        f"WHERE grain = 'hour' AND bucket < now() - interval '{hourly} days'",
        "rollups_daily": "DELETE FROM app.metric_rollups "
        f"WHERE grain = 'day' AND bucket < now() - interval '{daily} days'",
        "query_cache": "UPDATE app.query_runs SET result_cache = NULL "
        "WHERE cache_expires_at < now() - interval '1 day' "
        "AND result_cache IS NOT NULL",
        "query_runs": "DELETE FROM app.query_runs WHERE created_at < now() - interval '90 days'",
        "connection_tests": "DELETE FROM app.connection_tests WHERE created_at < now() - interval '30 days'",
        "catalog_syncs": "DELETE FROM app.catalog_syncs WHERE started_at < now() - interval '30 days' "
        "AND id NOT IN (SELECT last_seen_sync FROM app.catalog_tables WHERE last_seen_sync IS NOT NULL)",
        "table_stats": f"DELETE FROM app.table_stats_snapshots WHERE taken_at < now() - interval '{daily} days'",
        "notifications": "DELETE FROM app.notifications WHERE read_at < now() - interval '90 days'",
        "alert_events": "DELETE FROM app.alert_events "
        f"WHERE status = 'resolved' AND resolved_at < now() - interval '{daily} days'",
        "deleted_connections": "DELETE FROM app.connections WHERE deleted_at < now() - interval '7 days'",
        "sessions": "DELETE FROM app.sessions WHERE expires_at < now() - interval '1 day'",
        "user_tokens": "DELETE FROM app.user_tokens WHERE expires_at < now() - interval '7 days'",
        "login_attempts": "DELETE FROM app.login_attempts WHERE created_at < now() - interval '30 days'",
        "invitations": "DELETE FROM app.invitations "
        "WHERE accepted_at IS NULL AND expires_at < now() - interval '30 days'",
        "jobs": "DELETE FROM app.jobs "
        "WHERE status IN ('done', 'failed', 'cancelled') AND finished_at < now() - interval '7 days'",
        "audit_log": "DELETE FROM app.audit_log WHERE created_at < now() - interval '1 year'",
        "workspaces": "DELETE FROM app.workspaces WHERE deleted_at < now() - interval '30 days'",
    }
    with ctx.db.transaction() as conn:
        counts = {name: conn.execute(sql).rowcount for name, sql in statements.items()}
    logger.info("Retention removed %s", counts)
