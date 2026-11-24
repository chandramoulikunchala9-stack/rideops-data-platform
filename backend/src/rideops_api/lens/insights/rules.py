"""Insight rules (§16.4). Pure functions over the catalog + latest stats + connection context, each
yielding Insight(code, severity, object_ref, title, detail, recommendation)."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

MB = 1024 * 1024


@dataclass(frozen=True)
class Insight:
    code: str
    severity: str  # info | warning | critical
    object_ref: str
    title: str
    detail: dict[str, Any] = field(default_factory=dict)
    recommendation: str | None = None


@dataclass
class Context:
    tables: list[dict[str, Any]]  # catalog_tables rows (live)
    stats: dict[str, dict[str, Any]]  # "schema.table" -> latest table_stats_snapshots row
    stats_week_ago: dict[str, dict[str, Any]]  # "schema.table" -> snapshot ~7 days ago
    environment: str = "dev"
    tls_mode: str = "require"
    privilege_findings: list[dict[str, Any]] = field(default_factory=list)
    recent_metrics: list[dict[str, Any]] = field(default_factory=list)  # newest first
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _index_signature(index: dict[str, Any]) -> str:
    definition = index.get("definition") or ""
    if not definition:
        return ",".join(index["columns"])
    # "CREATE [UNIQUE] INDEX name ON schema.table USING btree (cols) [WHERE ...]" -> drop the name
    head, sep, rest = definition.partition(" ON ")
    return ("UNIQUE " if "UNIQUE" in head else "") + rest if sep else definition


def _ref(t: dict[str, Any]) -> str:
    return f"{t['schema_name']}.{t['table_name']}"


def stale_tables(ctx: Context) -> Iterator[Insight]:
    for t in ctx.tables:
        s = ctx.stats.get(_ref(t))
        if not t.get("freshness_column") or not s or not s.get("freshness_max"):
            continue
        last = s["freshness_max"]
        if isinstance(last, str):
            last = datetime.fromisoformat(last)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        lag = ctx.now - last
        if lag > timedelta(days=7):
            yield Insight("TABLE_STALE", "warning", _ref(t), f"{_ref(t)} is stale",
                          {"column": t["freshness_column"], "last_value": last.isoformat(), "lag_days": lag.days},
                          f"No new rows in {t['freshness_column']} for {lag.days} days. Check the loader that "
                          "writes this table.")  # fmt: skip


def missing_primary_keys(ctx: Context) -> Iterator[Insight]:
    for t in ctx.tables:
        if t["kind"] == "table" and not t.get("primary_key"):
            yield Insight("NO_PRIMARY_KEY", "info", _ref(t), f"{_ref(t)} has no primary key", {},
                          "Add a primary key so rows can be identified, replicated and deduplicated.")  # fmt: skip


def index_rules(ctx: Context) -> Iterator[Insight]:
    for t in ctx.tables:
        idx = [i for i in t.get("indexes", []) if i.get("columns")]
        for i in idx:
            if (
                not i.get("primary")
                and not i.get("unique")
                and (i.get("scans") or 0) == 0
                and (i.get("bytes") or 0) > 10 * MB
            ):
                yield Insight(
                    "UNUSED_INDEX",
                    "info",
                    f"{_ref(t)}#{i['name']}",
                    f"Unused index {i['name']}",
                    {"bytes": i["bytes"], "table": _ref(t)},
                    "No scans since statistics were reset. Consider dropping it to save space and write time.",
                )
        seen: dict[str, str] = {}
        for i in idx:
            # Compare full definitions minus the index name, so expression and partial indexes
            # (whose plain column list can look identical) are not reported as duplicates.
            key = _index_signature(i)
            if key in seen:
                yield Insight("DUPLICATE_INDEX", "info", f"{_ref(t)}#{i['name']}",
                              f"{i['name']} duplicates {seen[key]}", {"columns": i["columns"]},
                              "Two indexes cover the same columns; keep one.")  # fmt: skip
            else:
                seen[key] = i["name"]
        leading = {i["columns"][0] for i in idx}
        for fk in t.get("foreign_keys", []):
            if fk["columns"] and fk["columns"][0] not in leading:
                yield Insight(
                    "FK_WITHOUT_INDEX",
                    "info",
                    f"{_ref(t)}({','.join(fk['columns'])})",
                    f"Foreign key {_ref(t)}.{fk['columns'][0]} is not indexed",
                    {"references": fk["ref_table"]},
                    "Index the foreign-key column: joins and deletes on the parent table scan it otherwise.",
                )


def stats_rules(ctx: Context) -> Iterator[Insight]:
    for ref, s in ctx.stats.items():
        live = s.get("row_estimate") or 0
        dead = s.get("dead_rows") or 0
        if live + dead > 1000 and dead / (live + dead) > 0.20:
            yield Insight(
                "HIGH_DEAD_TUPLES",
                "warning",
                ref,
                f"{ref} has {dead / (live + dead):.0%} dead rows",
                {"dead_rows": dead, "live_rows": live},
                "Check autovacuum settings or run VACUUM.",
            )
        seq, ids = s.get("seq_scans") or 0, s.get("idx_scans") or 0
        if live > 100_000 and seq > 10 * max(ids, 1):
            yield Insight("SEQ_SCAN_HEAVY", "info", ref, f"{ref} is read mostly by full scans",
                          {"seq_scans": seq, "idx_scans": ids, "rows": live},
                          "Queries on this table may be missing an index.")  # fmt: skip
        old = ctx.stats_week_ago.get(ref)
        if old and (old.get("row_estimate") or 0) >= 1000 and live > 1.5 * old["row_estimate"]:
            growth = live / old["row_estimate"] - 1
            yield Insight("TABLE_GROWING_FAST", "info", ref, f"{ref} grew {growth:.0%} in 7 days",
                          {"rows_before": old["row_estimate"], "rows_now": live}, None)  # fmt: skip


def connection_rules(ctx: Context) -> Iterator[Insight]:
    writes = [f for f in ctx.privilege_findings if f.get("severity") == "warning"]
    if writes:
        yield Insight("OVER_PRIVILEGED_ROLE", "warning", "", "Lens connects with a role that can write",
                      {"privileges": sorted({f["privilege"] for f in writes})},
                      "Use a read-only role. The connection settings show the exact GRANT script.")  # fmt: skip
    if ctx.environment == "prod" and ctx.tls_mode == "require":
        yield Insight("TLS_WEAK", "warning", "", "Production connection doesn't verify the server certificate", {},
                      "Switch TLS to verify-full so a man-in-the-middle can't impersonate the database.")  # fmt: skip
    latest = ctx.recent_metrics[0] if ctx.recent_metrics else {}
    xid = latest.get("pg.xid_age_pct")
    if xid is not None and xid > 80:
        yield Insight("XID_WRAPAROUND_RISK", "critical", "", f"Transaction ID age at {xid:.0f}% of wraparound",
                      {"xid_age_pct": xid}, "Run VACUUM (FREEZE) on the oldest tables now.")  # fmt: skip
    last3 = [m.get("connections.used_pct") for m in ctx.recent_metrics[:3]]
    if len(last3) == 3 and all(v is not None and v > 80 for v in last3):
        yield Insight("CONNECTIONS_NEAR_MAX", "warning", "", "Connections above 80% of max for 3 checks",
                      {"used_pct": last3}, "Add a pooler (PgBouncer) or raise max_connections.")  # fmt: skip


RULES = (stale_tables, missing_primary_keys, index_rules, stats_rules, connection_rules)


def evaluate(ctx: Context) -> list[Insight]:
    found: dict[tuple[str, str], Insight] = {}
    for rule in RULES:
        for insight in rule(ctx):
            found.setdefault((insight.code, insight.object_ref), insight)
    return list(found.values())
