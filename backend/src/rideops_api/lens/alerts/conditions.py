"""Alert rule conditions (§17). Each evaluator returns Outcome(breached, value, summary).
`breached=None` means "not enough data": the rule neither fires nor counts towards resolving."""

import operator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

OPS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}
BAD_STATES = ["down", "unreachable", "auth_failed", "tls_error"]


@dataclass
class Outcome:
    breached: bool | None
    value: Any = None
    summary: str = ""


@dataclass
class EvalContext:
    connection: dict[str, Any]
    snapshots: list[
        dict[str, Any]
    ]  # newest first: state, reachable, latency_ms, metrics, error_code
    table_stats: dict[str, list[dict[str, Any]]] = field(
        default_factory=dict
    )  # "s.t" -> newest first
    schema_changes: list[dict[str, Any]] = field(default_factory=list)  # newest first
    privilege_findings: list[dict[str, Any]] = field(default_factory=list)
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _name(ctx: EvalContext) -> str:
    return ctx.connection["name"]


def status(cond: dict[str, Any], ctx: EvalContext) -> Outcome:
    n = max(1, int(cond.get("for_checks", 2)))
    states = cond.get("state_in") or BAD_STATES
    recent = ctx.snapshots[:n]
    if len(recent) < n:
        return Outcome(None)
    breached = all(s["state"] in states for s in recent)
    latest = recent[0]
    detail = f" ({latest['error_code']})" if latest.get("error_code") else ""
    return Outcome(
        breached, latest["state"], f"{_name(ctx)} is {latest['state']} for {n} checks{detail}"
    )


def metric(cond: dict[str, Any], ctx: EvalContext) -> Outcome:
    name, op, threshold = cond["metric"], cond.get("op", ">"), float(cond["value"])
    n = max(1, int(cond.get("for_checks", 1)))
    values = []
    for s in ctx.snapshots[:n]:
        v = s["latency_ms"] if name == "latency_ms" else (s.get("metrics") or {}).get(name)
        if v is None:
            return Outcome(None)
        values.append(float(v))
    if len(values) < n:
        return Outcome(None)
    breached = all(OPS[op](v, threshold) for v in values)
    return Outcome(
        breached, values[0], f"{_name(ctx)}: {name} {values[0]:g} {op} {threshold:g} for {n} checks"
    )


def _as_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def freshness(cond: dict[str, Any], ctx: EvalContext) -> Outcome:
    table = cond["freshness_table"]
    rows = ctx.table_stats.get(table) or []
    last = _as_dt(rows[0].get("freshness_max")) if rows else None
    if last is None:
        return Outcome(None)
    lag = (ctx.now - last).total_seconds() / 60
    limit = float(cond.get("max_lag_minutes", 120))
    return Outcome(
        lag > limit,
        round(lag, 1),
        f"{table} on {_name(ctx)}: newest row {lag:.0f} min old (limit {limit:g})",
    )


def row_drop(cond: dict[str, Any], ctx: EvalContext) -> Outcome:
    table = cond["row_drop_table"]
    window = {"1h": timedelta(hours=1), "1d": timedelta(days=1), "7d": timedelta(days=7)}.get(
        cond.get("window", "1d"), timedelta(days=1)
    )
    rows = ctx.table_stats.get(table) or []
    if not rows or rows[0].get("row_estimate") is None:
        return Outcome(None)
    now_rows = rows[0]["row_estimate"]
    then = next(
        (r for r in rows if _as_dt(r["taken_at"]) <= ctx.now - window and r.get("row_estimate")),
        None,
    )
    if then is None:
        return Outcome(None)
    drop = 100.0 * (then["row_estimate"] - now_rows) / then["row_estimate"]
    limit = float(cond.get("drop_pct", 30))
    return Outcome(
        drop >= limit,
        round(drop, 1),
        f"{table} on {_name(ctx)}: rows fell {drop:.0f}% ({then['row_estimate']:,} → {now_rows:,})",
    )


def schema_change(cond: dict[str, Any], ctx: EvalContext) -> Outcome:
    types = set(
        cond.get("schema_change_types") or ["table_dropped", "column_dropped", "type_changed"]
    )
    window = timedelta(minutes=int(cond.get("window_minutes", 60)))
    hits = [
        c
        for c in ctx.schema_changes
        if c["change_type"] in types and _as_dt(c["detected_at"]) >= ctx.now - window
    ]
    refs = [f"{c['change_type']} {c['object_ref']}" for c in hits[:5]]
    return Outcome(
        bool(hits), len(hits), f"{_name(ctx)}: {len(hits)} schema change(s): {'; '.join(refs)}"
    )


def privilege(cond: dict[str, Any], ctx: EvalContext) -> Outcome:
    writes = sorted(
        {f["privilege"] for f in ctx.privilege_findings if f.get("severity") == "warning"}
    )
    return Outcome(bool(writes), writes, f"{_name(ctx)}: Lens role can write ({', '.join(writes)})")


EVALUATORS = {"status": status, "metric": metric, "freshness": freshness, "row_drop": row_drop,
              "schema_change": schema_change, "privilege": privilege}  # fmt: skip


def validate_condition(rule_type: str, cond: dict[str, Any]) -> str | None:
    """Return an error message, or None if the condition is usable."""
    if rule_type not in EVALUATORS:
        return f"Unknown rule type {rule_type!r}."
    required = {
        "metric": ["metric", "value"],
        "freshness": ["freshness_table"],
        "row_drop": ["row_drop_table"],
    }
    missing = [k for k in required.get(rule_type, []) if k not in cond]
    if missing:
        return f"Missing condition field(s): {', '.join(missing)}."
    if rule_type == "metric" and cond.get("op", ">") not in OPS:
        return f"Unknown operator {cond.get('op')!r}."
    if rule_type == "status" and not set(cond.get("state_in") or BAD_STATES) <= {
        *BAD_STATES,
        "degraded",
        "unknown",
    }:
        return "state_in contains an unknown state."
    return None


DEFAULT_RULES = [  # FR-40: applied to every new connection
    {"name": "Database down", "rule_type": "status", "severity": "critical",
     "condition": {"state_in": BAD_STATES, "for_checks": 2}},
    {"name": "Latency above 2 s", "rule_type": "metric", "severity": "warning",
     "condition": {"metric": "latency_ms", "op": ">", "value": 2000, "for_checks": 3}},
    {"name": "Connections above 90%", "rule_type": "metric", "severity": "warning",
     "condition": {"metric": "connections.used_pct", "op": ">", "value": 90, "for_checks": 1}},
]  # fmt: skip
