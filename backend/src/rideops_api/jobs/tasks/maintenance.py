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
    """Delete expired auth rows and old finished jobs. Later phases extend this (snapshots,
    rollups, query results, connection tests) with the windows in RETENTION_* settings."""
    statements = {
        "sessions": "DELETE FROM app.sessions WHERE expires_at < now() - interval '1 day'",
        "user_tokens": "DELETE FROM app.user_tokens WHERE expires_at < now() - interval '7 days'",
        "login_attempts": "DELETE FROM app.login_attempts WHERE created_at < now() - interval '30 days'",
        "invitations": "DELETE FROM app.invitations WHERE accepted_at IS NULL AND expires_at < now() - interval '30 days'",
        "jobs": "DELETE FROM app.jobs WHERE status IN ('done','failed','cancelled') AND finished_at < now() - interval '7 days'",
        "audit_log": "DELETE FROM app.audit_log WHERE created_at < now() - interval '1 year'",
        "workspaces": "DELETE FROM app.workspaces WHERE deleted_at < now() - interval '30 days'",
    }
    with ctx.db.transaction() as conn:
        counts = {name: conn.execute(sql).rowcount for name, sql in statements.items()}
    logger.info("Retention removed %s", counts)
