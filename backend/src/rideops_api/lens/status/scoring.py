"""Health state and score (§15.1, §15.2). Pure functions, so every rule is unit-testable."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

UNREACHABLE_CODES = {"CONN_DNS_FAILED", "CONN_TIMEOUT", "CONN_REFUSED", "CONN_BLOCKED_NETWORK",
                     "CONN_PORT_NOT_ALLOWED"}  # fmt: skip
AUTH_CODES = {"CONN_AUTH_FAILED", "CONN_DB_NOT_FOUND"}
TLS_CODES = {"CONN_TLS_REQUIRED", "CONN_TLS_VERIFY_FAILED"}


@dataclass(frozen=True)
class Deduction:
    component: str
    points: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"component": self.component, "points": self.points, "reason": self.reason}


def p95(values: Sequence[float]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = max(0, int(round(0.95 * (len(vals) - 1))))
    return float(vals[k])


def score(metrics: dict[str, Any], *, recent_latencies: Sequence[float] = (),
          failed_in_last_10: int = 0) -> tuple[int, list[Deduction]]:  # fmt: skip
    """Start at 100 and subtract capped deductions. Returns (score, breakdown)."""
    out: list[Deduction] = []

    lat = p95([*recent_latencies, metrics.get("latency_ms")] if metrics.get("latency_ms") is not None
              else list(recent_latencies))  # fmt: skip
    if lat is not None:
        if lat > 2000:
            out.append(Deduction("latency", 25, f"p95 latency {lat:.0f} ms > 2000 ms"))
        elif lat > 500:
            out.append(Deduction("latency", 10, f"p95 latency {lat:.0f} ms > 500 ms"))

    used = metrics.get("connections.used_pct")
    if used is not None:
        if used > 95:
            out.append(Deduction("connections", 25, f"{used:.0f}% of max connections used"))
        elif used > 80:
            out.append(Deduction("connections", 10, f"{used:.0f}% of max connections used"))

    hit = metrics.get("cache.hit_ratio")
    if hit is not None:
        if hit < 0.90:
            out.append(Deduction("cache", 15, f"cache hit ratio {hit:.1%} < 90%"))
        elif hit < 0.95:
            out.append(Deduction("cache", 5, f"cache hit ratio {hit:.1%} < 95%"))

    lag = metrics.get("replication.lag_s")
    if lag is not None:
        if lag > 300:
            out.append(Deduction("replication", 30, f"replication lag {lag:.0f} s > 300 s"))
        elif lag > 30:
            out.append(Deduction("replication", 10, f"replication lag {lag:.0f} s > 30 s"))

    long_q = int(metrics.get("queries.long_running") or 0)
    if long_q:
        out.append(
            Deduction("long_queries", min(15, 5 * long_q), f"{long_q} queries running > 5 min")
        )

    if (metrics.get("locks.blocked") or 0) > 0:
        out.append(Deduction("blocking", 10, f"{metrics['locks.blocked']} blocked sessions"))

    if (metrics.get("deadlocks.delta") or 0) > 0:
        out.append(
            Deduction("deadlocks", 5, f"{metrics['deadlocks.delta']} deadlocks since last check")
        )

    if failed_in_last_10:
        out.append(Deduction("failed_checks", min(30, 5 * failed_in_last_10),
                             f"{failed_in_last_10} of the last 10 checks failed"))  # fmt: skip

    xid = metrics.get("pg.xid_age_pct")
    if xid is not None:
        if xid > 80:
            out.append(
                Deduction("wraparound", 40, f"transaction ID age at {xid:.0f}% of wraparound")
            )
        elif xid > 50:
            out.append(
                Deduction("wraparound", 10, f"transaction ID age at {xid:.0f}% of wraparound")
            )

    free = metrics.get("storage.free_pct")
    if free is not None:
        if free < 10:
            out.append(Deduction("storage", 30, f"only {free:.0f}% storage free"))
        elif free < 20:
            out.append(Deduction("storage", 10, f"only {free:.0f}% storage free"))

    return max(0, 100 - sum(d.points for d in out)), out


def state_for(*, reachable: bool, error_code: str | None, error_stage: str | None, health_score: int | None,
              paused: bool = False) -> str:  # fmt: skip
    if paused:
        return "paused"
    if error_code:
        if error_code in TLS_CODES or error_stage == "TLS":
            return "tls_error"
        if error_code in AUTH_CODES:
            return "auth_failed"
        if error_code in UNREACHABLE_CODES or error_stage in ("DNS", "NETWORK_POLICY", "TCP"):
            return "unreachable"
        return "down"
    if not reachable:
        return "unreachable"
    if health_score is None:
        return "unknown"
    if health_score >= 80:
        return "healthy"
    if health_score >= 50:
        return "degraded"
    return "down"


def with_deltas(
    metrics: dict[str, Any], previous: dict[str, Any] | None, elapsed_s: float | None
) -> dict[str, Any]:
    """Turn cumulative counters into *_delta and *_per_s using the previous snapshot (§15.3)."""
    out = dict(metrics)
    if not previous or not elapsed_s or elapsed_s <= 0:
        return out
    pairs = {"deadlocks.total": "deadlocks.delta"}
    rates = {"tx.commits.total": "tx.commits_per_s", "tx.rollbacks.total": "tx.rollbacks_per_s"}
    for src, dst in pairs.items():
        cur, prev = metrics.get(src), previous.get(src)
        if cur is not None and prev is not None and cur >= prev:  # a counter reset gives no delta
            out[dst] = cur - prev
    for src, dst in rates.items():
        cur, prev = metrics.get(src), previous.get(src)
        if cur is not None and prev is not None and cur >= prev:
            out[dst] = round((cur - prev) / elapsed_s, 3)
    return out
