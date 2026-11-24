"""Recompute insights after each catalog/stats refresh: upsert current ones, resolve vanished ones."""

import json
from typing import Any

from rideops_api.lens.insights import rules


def _latest_stats(
    conn, connection_id: Any, older_than_days: int | None = None
) -> dict[str, dict[str, Any]]:
    cond = (
        "" if older_than_days is None else "AND taken_at <= now() - make_interval(days => %(days)s)"
    )
    rows = conn.execute(
        f"""SELECT DISTINCT ON (schema_name, table_name) *
              FROM app.table_stats_snapshots WHERE connection_id = %(c)s {cond}
             ORDER BY schema_name, table_name, taken_at DESC""",
        {"c": connection_id, "days": older_than_days},
    ).fetchall()
    return {f"{r['schema_name']}.{r['table_name']}": r for r in rows}


def refresh(db: Any, connection_id: Any, connection_row: dict[str, Any]) -> int:
    with db.transaction() as conn:
        tables = conn.execute(
            "SELECT * FROM app.catalog_tables WHERE connection_id = %s AND dropped_at IS NULL",
            (connection_id,),
        ).fetchall()
        test = conn.execute(
            """SELECT privilege_findings FROM app.connection_tests WHERE connection_id = %s AND ok
                ORDER BY created_at DESC LIMIT 1""",
            (connection_id,),
        ).fetchone()
        metrics = [r["metrics"] for r in conn.execute(
            "SELECT metrics FROM app.status_snapshots WHERE connection_id = %s AND reachable "
            "ORDER BY taken_at DESC LIMIT 3", (connection_id,)).fetchall()]  # fmt: skip
        ctx = rules.Context(
            tables=tables,
            stats=_latest_stats(conn, connection_id),
            stats_week_ago=_latest_stats(conn, connection_id, older_than_days=7),
            environment=connection_row["environment"],
            tls_mode=connection_row["tls_mode"],
            privilege_findings=(test or {}).get("privilege_findings") or [],
            recent_metrics=metrics,
        )
        found = rules.evaluate(ctx)
        for i in found:
            conn.execute(
                """INSERT INTO app.insights (connection_id, code, severity, object_ref, title, detail, recommendation)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (connection_id, code, object_ref) DO UPDATE SET
                     severity = EXCLUDED.severity, title = EXCLUDED.title, detail = EXCLUDED.detail,
                     recommendation = EXCLUDED.recommendation, last_seen_at = now(), resolved_at = NULL""",
                (connection_id, i.code, i.severity, i.object_ref, i.title, json.dumps(i.detail, default=str),
                 i.recommendation),
            )  # fmt: skip
        keys = [f"{i.code}|{i.object_ref}" for i in found]
        conn.execute(
            """UPDATE app.insights SET resolved_at = now()
                WHERE connection_id = %s AND resolved_at IS NULL AND NOT (code || '|' || object_ref = ANY(%s))""",
            (connection_id, keys),
        )
    return len(found)
