"""Column profiling over a sample (FR-32, §14.4) and auto-chart specs (§16.3).

Statistics are computed in plain Python over at most PROFILE_MAX_ROWS sampled rows. PII columns
(name heuristics + value patterns) keep only value-free stats: null %, distinct, length.
"""

import json
import logging
import math
import re
import statistics
import time
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from rideops_api.jobs.registry import JobContext, job
from rideops_api.lens import pools
from rideops_api.lens.adapters import pg_catalog
from rideops_api.lens.catalog import detect
from rideops_api.lens.connections.service import TargetUnavailable, load_target
from rideops_api.settings import get_lens_settings

logger = logging.getLogger(__name__)
PROFILE_DEFAULT_ROWS = 10_000
PROFILE_MAX_ROWS = 100_000
TOP_K = 10
HIST_BINS = 20

PATTERNS = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
    "uuid": re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    ),
    "url": re.compile(r"^https?://\S+$"),
    "phone": re.compile(r"^\+?[0-9][0-9 ()-]{7,17}[0-9]$"),
    "numeric_string": re.compile(r"^-?[0-9]+(\.[0-9]+)?$"),
    "ip": re.compile(r"^(\d{1,3}\.){3}\d{1,3}$"),
}
PII_PATTERNS = {"email", "phone", "ip"}


def _kind(data_type: str, values: list[Any]) -> str:
    t = data_type.lower()
    if t in ("boolean", "bool"):
        return "boolean"
    if detect.is_temporal(t):
        return "temporal"
    if any(
        k in t for k in ("int", "numeric", "decimal", "real", "double", "float", "money", "serial")
    ):
        return "numeric"
    if t.startswith(("json", "jsonb")) or t.endswith("[]"):
        return "structured"
    if values and all(
        isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) for v in values[:50]
    ):
        return "numeric"
    return "text"


def _quantiles(sorted_vals: list[float]) -> dict[str, float]:
    def q(p: float) -> float:
        k = (len(sorted_vals) - 1) * p
        lo, hi = math.floor(k), math.ceil(k)
        return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)

    return {
        name: round(q(p), 6)
        for name, p in (("p01", 0.01), ("p25", 0.25), ("p50", 0.5), ("p75", 0.75), ("p99", 0.99))
    }


def _histogram(vals: list[float], bins: int = HIST_BINS) -> list[list[float]]:
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return [[lo, hi, len(vals)]]
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        counts[min(bins - 1, int((v - lo) / width))] += 1
    return [
        [round(lo + i * width, 6), round(lo + (i + 1) * width, 6), c] for i, c in enumerate(counts)
    ]


def profile_column(
    name: str, data_type: str, raw: list[Any], *, pii_by_name: bool
) -> tuple[dict[str, Any], dict | None]:
    """Return (profile, chart spec or None) for one column of the sample."""
    n = len(raw)
    values = [v for v in raw if v is not None]
    kind = _kind(data_type, values)
    distinct = len(
        {json.dumps(pg_catalog.jsonable(v), sort_keys=True, default=str) for v in values}
    )
    prof: dict[str, Any] = {"column": name, "data_type": data_type, "kind": kind, "sample_size": n,
                            "null_pct": round(100.0 * (n - len(values)) / n, 2) if n else None,
                            "distinct_est": distinct, "pii": pii_by_name}  # fmt: skip
    texts = [v for v in values if isinstance(v, str)]
    if texts:
        lengths = [len(t) for t in texts]
        prof["length"] = {
            "min": min(lengths),
            "max": max(lengths),
            "mean": round(statistics.fmean(lengths), 2),
        }
        sample = texts[:2000]
        patterns = {
            k: round(sum(1 for t in sample if rx.match(t)) / len(sample), 3)
            for k, rx in PATTERNS.items()
        }
        prof["patterns"] = {k: v for k, v in patterns.items() if v > 0}
        if any(prof["patterns"].get(p, 0) >= 0.5 for p in PII_PATTERNS):
            prof["pii"] = True  # value pattern says personal data even if the name didn't
    if prof["pii"]:
        chart = None
        if "length" in prof:
            chart = {"type": "stats", "title": f"{name}: length {prof['length']['min']}–{prof['length']['max']}",
                     "note": "PII: values hidden"}  # fmt: skip
        return prof, chart

    chart: dict[str, Any] | None = None
    if kind == "numeric" and values:
        nums = sorted(
            float(v)
            for v in values
            if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)
        )
        if nums:
            prof.update(min=nums[0], max=nums[-1], mean=round(statistics.fmean(nums), 6),
                        stddev=round(statistics.pstdev(nums), 6) if len(nums) > 1 else 0.0,
                        quantiles=_quantiles(nums))  # fmt: skip
            if distinct > 20:
                prof["histogram"] = _histogram(nums)
                chart = {
                    "type": "histogram",
                    "title": f"Distribution of {name}",
                    "bins": prof["histogram"],
                }
    elif kind == "temporal" and values:
        stamps = sorted(v for v in values if isinstance(v, (datetime, date)))
        if stamps:
            prof.update(min=stamps[0].isoformat(), max=stamps[-1].isoformat())
            per_day = Counter(
                (s.date() if isinstance(s, datetime) else s).isoformat() for s in stamps
            )
            series = sorted(per_day.items())[-90:]
            chart = {"type": "timeseries", "title": f"Rows per day by {name} (sampled)",
                     "points": [[d, c] for d, c in series]}  # fmt: skip
    if kind in ("text", "boolean", "numeric") and values and (distinct <= 20 or kind != "numeric"):
        top = Counter(json.dumps(pg_catalog.jsonable(v), default=str) for v in values).most_common(
            TOP_K
        )
        prof["top_k"] = [[json.loads(k), c] for k, c in top]
        if distinct <= 20 or kind == "boolean":
            chart = {"type": "bar", "title": f"Top values of {name}", "bars": prof["top_k"]}
        elif kind == "text" and "length" in prof and chart is None:
            chart = {
                "type": "patterns",
                "title": f"{name}: patterns",
                "patterns": prof.get("patterns", {}),
            }
    if kind == "text" and texts and "min" not in prof:
        prof.update(min=min(texts)[:64], max=max(texts)[:64])
    return prof, chart


def profile(db: Any, connection_id: Any, schema: str, table: str, *, sample_rows: int = PROFILE_DEFAULT_ROWS,
            columns: list[str] | None = None, settings=None) -> dict[str, Any]:  # fmt: skip
    settings = settings or get_lens_settings()
    sample_rows = max(100, min(int(sample_rows), PROFILE_MAX_ROWS))
    with db.pool.connection() as conn:
        meta = conn.execute(
            """SELECT t.columns, t.pii_columns,
                      (SELECT row_estimate FROM app.table_stats_snapshots s WHERE s.connection_id = t.connection_id
                          AND s.schema_name = t.schema_name AND s.table_name = t.table_name
                        ORDER BY taken_at DESC LIMIT 1) AS row_estimate
                 FROM app.catalog_tables t
                WHERE t.connection_id = %s AND t.schema_name = %s AND t.table_name = %s AND t.dropped_at IS NULL""",
            (connection_id, schema, table),
        ).fetchone()
    if meta is None:
        raise TargetUnavailable(
            "NOT_FOUND", "Table not found in the catalog. Sync the schema first."
        )
    known = [c["name"] for c in meta["columns"]]
    cols = [c for c in (columns or known) if c in known][
        :200
    ]  # only catalog names, never free text
    types = {c["name"]: c["data_type"] for c in meta["columns"]}
    _, adapter = load_target(db, connection_id, settings)
    started = time.perf_counter()
    with (
        pools.target_slot(str(connection_id), settings.max_target_sessions, wait_s=10.0),
        adapter.connect() as t,
    ):
        rows, method = adapter.sample(t, schema, table, cols, sample_rows, meta["row_estimate"])
    profiles, charts = [], []
    pii = set(meta["pii_columns"])
    for c in cols:
        prof, chart = profile_column(c, types[c], [r[c] for r in rows], pii_by_name=c in pii)
        profiles.append(prof)
        if chart:
            charts.append({"column": c, **chart})
    elapsed = int((time.perf_counter() - started) * 1000)
    out = {"sample_rows": len(rows), "method": method, "elapsed_ms": elapsed,
           "profiles": [pg_catalog.jsonable(p) for p in profiles], "charts": pg_catalog.jsonable(charts)}  # fmt: skip
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO app.column_profiles (connection_id, schema_name, table_name, sample_rows, method,
                      elapsed_ms, profiles, charts) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (connection_id, schema, table, len(rows), method, elapsed, json.dumps(out["profiles"]),
             json.dumps(out["charts"])),
        )  # fmt: skip
        newly_pii = [p["column"] for p in profiles if p["pii"] and p["column"] not in pii]
        if newly_pii:  # value patterns found personal data the column name didn't reveal
            conn.execute(
                """UPDATE app.catalog_tables SET pii_columns = (SELECT array_agg(DISTINCT x) FROM
                          unnest(pii_columns || %s::text[]) x)
                    WHERE connection_id = %s AND schema_name = %s AND table_name = %s""",
                (newly_pii, connection_id, schema, table),
            )
    return out


@job("profile_table", timeout_s=120, max_attempts=2, priority=30)
def profile_table_job(ctx: JobContext) -> None:
    p = ctx.payload
    try:
        profile(ctx.db, ctx.job["connection_id"], p["schema"], p["table"], sample_rows=p.get("sample_rows",
                PROFILE_DEFAULT_ROWS), columns=p.get("columns"), settings=ctx.settings)  # fmt: skip
    except TargetUnavailable as exc:
        logger.info("profile skipped: %s", exc.message)
