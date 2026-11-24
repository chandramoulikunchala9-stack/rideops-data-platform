"""Lens system endpoints (§13.8): liveness, readiness, and the internal scheduler tick."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from rideops_api import hardening, migrate
from rideops_api.auth.deps import LensSettingsDep, MetaDbDep
from rideops_api.auth.tokens import same
from rideops_api.errors import ApiError
from rideops_api.jobs import scheduler
from rideops_api.jobs.worker import Worker
from rideops_api.lens.connections.service import keyring_for
from rideops_api.lens.security.crypto import KeyRingError

router = APIRouter(tags=["system"])
TICK_MAX_JOBS = 20


@router.get("/healthz")
def healthz() -> dict:
    """Liveness: the process is up. Never touches the database."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(db: MetaDbDep, settings: LensSettingsDep) -> JSONResponse:
    """Readiness: metadata DB reachable, migrated to head, and the encryption key ring loaded."""
    checks: dict[str, str] = {}
    try:
        with db.pool.connection() as conn:
            current = migrate.current_revision(conn)
        head = migrate.head_revision()
        checks["database"] = "ok"
        checks["migrations"] = "ok" if current == head else f"at {current}, expected {head}"
    except Exception:  # noqa: BLE001 - any failure means not ready
        checks["database"] = "unavailable"
        checks["migrations"] = "unknown"
    try:
        keyring_for(settings)
        checks["keys"] = "ok"
    except KeyRingError as exc:
        checks["keys"] = str(exc)
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ready" if ready else "not_ready", "checks": checks},
                        status_code=200 if ready else 503)  # fmt: skip


@router.post("/internal/tick")
def internal_tick(request: Request, db: MetaDbDep, settings: LensSettingsDep) -> dict:
    """Wake a sleeping free-tier instance: run one scheduler tick and drain a few jobs.
    Called by an external cron with `X-Internal-Token`. Disabled when INTERNAL_TOKEN is unset."""
    token = settings.internal_token.get_secret_value() if settings.internal_token else ""
    given = request.headers.get("x-internal-token", "")
    if not token:
        raise ApiError(404, "NOT_FOUND", "Not found.")
    if not given or not same(given, token):
        raise ApiError(403, "FORBIDDEN", "Invalid internal token.")
    result = scheduler.tick(db)
    ran = Worker(db, settings=settings).drain(TICK_MAX_JOBS)
    return {
        "leader": result.leader,
        "enqueued": result.enqueued,
        "reaped": result.reaped,
        "jobs_run": ran,
    }


def _internal_auth(request: Request, settings) -> None:
    token = settings.internal_token.get_secret_value() if settings.internal_token else ""
    given = request.headers.get("x-internal-token", "")
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        given = given or auth[7:]
    if not token:
        raise ApiError(404, "NOT_FOUND", "Not found.")
    if not given or not same(given, token):
        raise ApiError(403, "FORBIDDEN", "Invalid internal token.")


def _esc(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request, db: MetaDbDep, settings: LensSettingsDep) -> str:
    """Prometheus text format (§21). Token-protected like /internal/tick (header or Bearer)."""
    _internal_auth(request, settings)
    lines: list[str] = []

    def gauge(name: str, help_text: str, samples: list[tuple[dict, float]]) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        for labels, value in samples:
            lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
            lines.append(
                f"{name}{{{lab}}} {float(value or 0)}" if lab else f"{name} {float(value or 0)}"
            )

    with db.pool.connection() as conn:
        q = conn.execute(
            "SELECT job_type, count(*) AS n FROM app.jobs WHERE status = 'queued' GROUP BY 1"
        ).fetchall()
        gauge(
            "lens_jobs_queued",
            "Queued jobs by type",
            [({"type": r["job_type"]}, r["n"]) for r in q],
        )
        oldest = conn.execute(
            "SELECT coalesce(extract(epoch FROM now() - min(run_at)), 0) AS s FROM app.jobs "
            "WHERE status = 'queued' AND run_at <= now()"
        ).fetchone()["s"]
        gauge(
            "lens_job_oldest_queued_seconds", "Age of the oldest due job (backlog)", [({}, oldest)]
        )
        running = conn.execute(
            "SELECT count(*) AS n FROM app.jobs WHERE status = 'running'"
        ).fetchone()["n"]
        gauge("lens_jobs_running", "Jobs currently running", [({}, running)])
        beat = conn.execute(
            "SELECT coalesce(extract(epoch FROM now() - max(greatest(heartbeat_at, finished_at, locked_at))), "
            "-1) AS s FROM app.jobs"
        ).fetchone()["s"]
        gauge(
            "lens_worker_last_activity_seconds",
            "Seconds since any worker heartbeat or finished job",
            [({}, beat)],
        )
        failed = conn.execute(
            "SELECT job_type, count(*) AS n FROM app.jobs WHERE status = 'failed' "
            "AND finished_at > now() - interval '1 hour' GROUP BY 1"
        ).fetchall()
        gauge(
            "lens_jobs_failed_1h",
            "Jobs failed in the last hour",
            [({"type": r["job_type"]}, r["n"]) for r in failed],
        )
        states = conn.execute(
            "SELECT current_state::text AS s, count(*) AS n FROM app.connections "
            "WHERE deleted_at IS NULL GROUP BY 1"
        ).fetchall()
        gauge(
            "lens_connections",
            "Connections by state",
            [({"state": r["s"]}, r["n"]) for r in states],
        )
        lat = conn.execute(
            "SELECT coalesce(avg(latency_ms), 0) AS v FROM app.status_snapshots "
            "WHERE taken_at > now() - interval '1 hour' AND reachable"
        ).fetchone()["v"]
        gauge(
            "lens_target_check_latency_ms", "Average target check latency, last hour", [({}, lat)]
        )
        ssrf = conn.execute(
            "SELECT count(*) AS n FROM app.audit_log WHERE action = 'network.blocked'"
        ).fetchone()["n"]
        gauge(
            "lens_ssrf_blocks_total",
            "Connection attempts blocked by the Network Guard",
            [({}, ssrf)],
        )
        rej = conn.execute(
            "SELECT coalesce(error_code, 'unknown') AS r, count(*) AS n FROM app.query_runs "
            "WHERE status = 'rejected' GROUP BY 1"
        ).fetchall()
        gauge(
            "lens_query_rejections_total",
            "Statements rejected by the SQL guard",
            [({"reason": r["r"]}, r["n"]) for r in rej],
        )
    gauge(
        "lens_api_requests_total",
        "API requests since process start",
        [
            ({"route": route, "status": status}, n)
            for (route, status), n in sorted(hardening.METRICS.snapshot().items())
        ],
    )
    return "\n".join(lines) + "\n"
