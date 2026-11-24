"""Schema sync + stats refresh (FR-30/31, §14.3): catalog-only reads, diff → schema_changes,
freshness and PII detection, one stats snapshot per table, then insights."""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from rideops_api.jobs.registry import JobContext, job
from rideops_api.lens import pools
from rideops_api.lens.adapters import pg_catalog
from rideops_api.lens.catalog import detect
from rideops_api.lens.connections.service import TargetUnavailable, load_target
from rideops_api.lens.queries import sqlguard
from rideops_api.settings import get_lens_settings

logger = logging.getLogger(__name__)
FRESHNESS_EXACT_MAX_ROWS = (
    1_000_000  # unindexed freshness columns: only query max() below this size
)
FRESHNESS_MAX_TABLES = 100


def _ref(t: dict[str, Any]) -> str:
    return f"{t['schema']}.{t['name']}"


def diff(old: dict[str, dict[str, Any]], new: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare two catalogs keyed by 'schema.table'. Returns schema_changes rows (§12 change types)."""
    changes: list[dict[str, Any]] = []
    for ref in sorted(new.keys() - old.keys()):
        changes.append({"change_type": "table_added", "object_ref": ref, "before": None,
                        "after": {"kind": new[ref]["kind"], "columns": len(new[ref]["columns"])}})  # fmt: skip
    for ref in sorted(old.keys() - new.keys()):
        changes.append({"change_type": "table_dropped", "object_ref": ref, "before": {"kind": old[ref]["kind"]},
                        "after": None})  # fmt: skip
    for ref in sorted(old.keys() & new.keys()):
        before = {c["name"]: c for c in old[ref]["columns"]}
        after = {c["name"]: c for c in new[ref]["columns"]}
        for col in sorted(after.keys() - before.keys()):
            changes.append({"change_type": "column_added", "object_ref": f"{ref}.{col}", "before": None,
                            "after": {"data_type": after[col]["data_type"]}})  # fmt: skip
        for col in sorted(before.keys() - after.keys()):
            changes.append({"change_type": "column_dropped", "object_ref": f"{ref}.{col}",
                            "before": {"data_type": before[col]["data_type"]}, "after": None})  # fmt: skip
        for col in sorted(before.keys() & after.keys()):
            b, a = before[col], after[col]
            if b["data_type"] != a["data_type"]:
                changes.append(
                    {
                        "change_type": "type_changed",
                        "object_ref": f"{ref}.{col}",
                        "before": {"data_type": b["data_type"]},
                        "after": {"data_type": a["data_type"]},
                    }
                )
            if b["nullable"] != a["nullable"]:
                changes.append(
                    {
                        "change_type": "nullability_changed",
                        "object_ref": f"{ref}.{col}",
                        "before": {"nullable": b["nullable"]},
                        "after": {"nullable": a["nullable"]},
                    }
                )
        bi = {i["name"]: i for i in old[ref].get("indexes", [])}
        ai = {i["name"]: i for i in new[ref].get("indexes", [])}
        for name in sorted(ai.keys() - bi.keys()):
            changes.append({"change_type": "index_added", "object_ref": f"{ref}#{name}", "before": None,
                            "after": {"columns": ai[name]["columns"]}})  # fmt: skip
        for name in sorted(bi.keys() - ai.keys()):
            changes.append({"change_type": "index_dropped", "object_ref": f"{ref}#{name}",
                            "before": {"columns": bi[name]["columns"]}, "after": None})  # fmt: skip
    return changes


def sync(
    db: Any, connection_id: Any, settings=None, *, include_schema: bool = True
) -> dict[str, Any]:
    settings = settings or get_lens_settings()
    row, adapter = load_target(db, connection_id, settings)
    with db.pool.connection() as conn:
        sync_id = conn.execute("INSERT INTO app.catalog_syncs (connection_id) VALUES (%s) RETURNING id",
                               (connection_id,)).fetchone()["id"]  # fmt: skip
    try:
        with (
            pools.target_slot(str(connection_id), settings.max_target_sessions, wait_s=5.0),
            adapter.connect() as t,
        ):
            tables = pg_catalog.list_tables(t) if include_schema else None
            stats = pg_catalog.table_stats(t)
            if row["is_demo"]:  # never expose Lens's own metadata through the shared demo database
                hidden = sqlguard.DEMO_FORBIDDEN_SCHEMAS
                tables = (
                    [tb for tb in tables if tb["schema"] not in hidden]
                    if tables is not None
                    else None
                )
                stats = [s for s in stats if s["schema"] not in hidden]
            if tables is None:
                with db.pool.connection() as conn:
                    known = conn.execute(
                        """SELECT schema_name AS schema, table_name AS name, kind, columns, indexes, freshness_column
                             FROM app.catalog_tables WHERE connection_id = %s AND dropped_at IS NULL""",
                        (connection_id,),
                    ).fetchall()
                fresh_cols = {f"{k['schema']}.{k['name']}": k["freshness_column"] for k in known}
                index_first = {f"{k['schema']}.{k['name']}": {i["columns"][0] for i in k["indexes"] if i["columns"]}
                               for k in known}  # fmt: skip
            else:
                fresh_cols = {_ref(tb): detect.freshness_column(tb) for tb in tables}
                index_first = {
                    _ref(tb): {i["columns"][0] for i in tb["indexes"] if i["columns"]}
                    for tb in tables
                }
            freshness: dict[str, Any] = {}
            for s in sorted(stats, key=lambda x: -(x["row_estimate"] or 0))[
                : FRESHNESS_MAX_TABLES * 5
            ]:
                ref = f"{s['schema']}.{s['name']}"
                col = fresh_cols.get(ref)
                if not col or len(freshness) >= FRESHNESS_MAX_TABLES:
                    continue
                if (
                    col in index_first.get(ref, set())
                    or (s["row_estimate"] or 0) < FRESHNESS_EXACT_MAX_ROWS
                ):
                    try:
                        freshness[ref] = pg_catalog.freshness_max(t, s["schema"], s["name"], col)
                    except Exception as exc:  # noqa: BLE001 - one table must not fail the sync
                        logger.info("freshness skipped for %s: %s", ref, exc)
    except Exception as exc:
        with db.pool.connection() as conn:
            conn.execute("UPDATE app.catalog_syncs SET finished_at = now(), ok = false, error = left(%s, 1000) "
                         "WHERE id = %s", (str(exc), sync_id))  # fmt: skip
        raise

    changes: list[dict[str, Any]] = []
    with db.transaction() as conn:
        if tables is not None:
            existing = {
                f"{r['schema_name']}.{r['table_name']}": {"kind": r["kind"], "columns": r["columns"],
                                                          "indexes": r["indexes"]}
                for r in conn.execute(
                    "SELECT * FROM app.catalog_tables WHERE connection_id = %s AND dropped_at IS NULL", (connection_id,)
                ).fetchall()
            }  # fmt: skip
            new = {_ref(tb): tb for tb in tables}
            first_sync = not conn.execute(
                "SELECT 1 FROM app.catalog_syncs WHERE connection_id = %s AND ok LIMIT 1",
                (connection_id,),
            ).fetchone()
            changes = [] if first_sync else diff(existing, new)  # the first sync is the baseline
            for tb in tables:
                conn.execute(
                    """INSERT INTO app.catalog_tables (connection_id, schema_name, table_name, kind, comment, columns,
                              indexes, foreign_keys, primary_key, freshness_column, pii_columns, last_seen_sync)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (connection_id, schema_name, table_name) DO UPDATE SET
                         kind = EXCLUDED.kind, comment = EXCLUDED.comment, columns = EXCLUDED.columns,
                         indexes = EXCLUDED.indexes, foreign_keys = EXCLUDED.foreign_keys,
                         primary_key = EXCLUDED.primary_key, freshness_column = EXCLUDED.freshness_column,
                         pii_columns = EXCLUDED.pii_columns, last_seen_sync = EXCLUDED.last_seen_sync,
                         dropped_at = NULL""",
                    (connection_id, tb["schema"], tb["name"], tb["kind"], tb["comment"],
                     json.dumps(tb["columns"], default=str), json.dumps(tb["indexes"], default=str),
                     json.dumps(tb["foreign_keys"]), tb["primary_key"], fresh_cols.get(_ref(tb)),
                     detect.pii_columns(tb), sync_id),
                )  # fmt: skip
            conn.execute(
                """UPDATE app.catalog_tables SET dropped_at = now()
                    WHERE connection_id = %s AND dropped_at IS NULL AND last_seen_sync IS DISTINCT FROM %s""",
                (connection_id, sync_id),
            )
            for ch in changes:
                conn.execute(
                    """INSERT INTO app.schema_changes (connection_id, change_type, object_ref, before, after)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (connection_id, ch["change_type"], ch["object_ref"], json.dumps(ch["before"]),
                     json.dumps(ch["after"])),
                )  # fmt: skip
        now = datetime.now(timezone.utc)
        for s in stats:
            ref = f"{s['schema']}.{s['name']}"
            conn.execute(
                """INSERT INTO app.table_stats_snapshots (connection_id, schema_name, table_name, taken_at,
                          row_estimate, total_bytes, table_bytes, index_bytes, dead_rows, seq_scans, idx_scans,
                          inserts, updates, deletes, last_vacuum, last_analyze, freshness_max)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (connection_id, s["schema"], s["name"], now, s["row_estimate"] if s["row_estimate"] is not None
                 else s["live_rows"], s["total_bytes"], s["table_bytes"], s["index_bytes"], s["dead_rows"],
                 s["seq_scans"], s["idx_scans"], s["inserts"], s["updates"], s["deletes"], s["last_vacuum"],
                 s["last_analyze"], freshness.get(ref)),
            )  # fmt: skip
        n_cols = sum(len(tb["columns"]) for tb in tables) if tables is not None else None
        conn.execute(
            """UPDATE app.catalog_syncs SET finished_at = now(), ok = true, table_count = %s, column_count = %s
                WHERE id = %s""",
            (len(tables) if tables is not None else len(stats), n_cols, sync_id),
        )
    from rideops_api.lens.insights import (
        service as insights,
    )  # local: insights reads the catalog we just wrote

    insights.refresh(db, connection_id, row)
    return {
        "sync_id": sync_id,
        "tables": len(tables) if tables is not None else len(stats),
        "changes": len(changes),
    }


@job("schema_sync", timeout_s=120, max_attempts=3, priority=50)
def schema_sync_job(ctx: JobContext) -> None:
    try:
        sync(ctx.db, ctx.job["connection_id"], ctx.settings, include_schema=True)
    except TargetUnavailable as exc:
        logger.info("schema_sync skipped for %s: %s", ctx.job["connection_id"], exc.message)


@job("stats_refresh", timeout_s=60, max_attempts=3, priority=40)
def stats_refresh_job(ctx: JobContext) -> None:
    try:
        sync(ctx.db, ctx.job["connection_id"], ctx.settings, include_schema=False)
    except TargetUnavailable as exc:
        logger.info("stats_refresh skipped for %s: %s", ctx.job["connection_id"], exc.message)
