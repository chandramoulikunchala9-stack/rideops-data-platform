"""Pipeline monitoring endpoints: list, history, logs, run details and retry."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status

from rideops_api import schemas
from rideops_api.errors import ApiError
from rideops_api.params import DbDep, PaginationDep
from rideops_api.runner import AlreadyRunningError, JobRunner

router = APIRouter(tags=["pipelines"])

StatusParam = Annotated[
    Literal["RUNNING", "SUCCESS", "FAILED", "CANCELLED"] | None, Query(alias="status")
]


def get_runner(request: Request) -> JobRunner:
    return request.app.state.runner


RunnerDep = Annotated[JobRunner, Depends(get_runner)]

_RUN_COLUMNS = """run_id, pipeline_name, layer, trigger, status, start_ts, end_ts, duration_sec,
    records_read, records_written, records_rejected, retry_count, error_message, run_source,
    retry_of_run_id"""


def _require_pipeline(db, name: str) -> None:
    if not db.scalar("SELECT 1 FROM ops.pipeline_runs WHERE pipeline_name = %s LIMIT 1", (name,)):
        raise ApiError(404, "not_found", f"Pipeline '{name}' has no recorded runs")


@router.get("/pipelines", response_model=list[schemas.PipelineSummary])
def list_pipelines(
    db: DbDep, runner: RunnerDep, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> list[schemas.PipelineSummary]:
    rows = db.fetch_all(
        """
        WITH latest AS (
            SELECT DISTINCT ON (pipeline_name) pipeline_name, layer, run_id, status, start_ts,
                   duration_sec, error_message
            FROM ops.pipeline_runs ORDER BY pipeline_name, start_ts DESC
        ),
        stats AS (
            SELECT pipeline_name,
                   count(*) FILTER (WHERE start_ts >= now() - make_interval(days => %(days)s)) AS runs,
                   round(count(*) FILTER (WHERE status = 'SUCCESS'
                                          AND start_ts >= now() - make_interval(days => %(days)s))::numeric
                         / nullif(count(*) FILTER (WHERE status <> 'RUNNING'
                                          AND start_ts >= now() - make_interval(days => %(days)s)), 0), 4)
                       AS success_rate,
                   max(end_ts) FILTER (WHERE status = 'SUCCESS') AS last_success_at,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_sec)
                       FILTER (WHERE status = 'SUCCESS') AS p50_duration_sec,
                   array_agg(DISTINCT run_source) AS run_sources
            FROM ops.pipeline_runs GROUP BY pipeline_name
        )
        SELECT l.pipeline_name, l.layer, l.run_id AS last_run_id, l.status AS last_status,
               l.start_ts AS last_run_at, l.duration_sec AS last_duration_sec,
               l.error_message AS last_error, s.runs, s.success_rate, s.last_success_at,
               s.p50_duration_sec, s.run_sources
        FROM latest l JOIN stats s USING (pipeline_name)
        ORDER BY (l.status = 'FAILED') DESC, l.pipeline_name
        """,
        {"days": days},
    )
    return [
        schemas.PipelineSummary(**r, retryable=runner.can_run(r["pipeline_name"])) for r in rows
    ]


@router.get("/pipelines/{name}", response_model=schemas.Page[schemas.Run])
def pipeline_runs(
    name: str, db: DbDep, page: PaginationDep, status_filter: StatusParam = None
) -> schemas.Page[schemas.Run]:
    _require_pipeline(db, name)
    params = {"name": name, "status": status_filter, "limit": page.limit, "offset": page.offset}
    where = "pipeline_name = %(name)s AND (%(status)s::text IS NULL OR status = %(status)s)"
    rows = db.fetch_all(
        f"SELECT {_RUN_COLUMNS} FROM ops.pipeline_runs WHERE {where} "  # noqa: S608 - constants only
        "ORDER BY start_ts DESC LIMIT %(limit)s OFFSET %(offset)s",
        params,
    )
    total = db.scalar(f"SELECT count(*) FROM ops.pipeline_runs WHERE {where}", params)  # noqa: S608
    return schemas.Page[schemas.Run](
        items=[schemas.Run(**r) for r in rows], total=total, limit=page.limit, offset=page.offset
    )


def _logs(db, run_id: str) -> list[dict]:
    return db.fetch_all(
        "SELECT logged_at, level, message FROM ops.pipeline_run_logs WHERE run_id = %s ORDER BY log_id",
        (run_id,),
    )


@router.get("/pipelines/{name}/logs", response_model=schemas.RunLogs)
def pipeline_logs(name: str, db: DbDep, run_id: str | None = None) -> schemas.RunLogs:
    _require_pipeline(db, name)
    run = db.fetch_one(
        """
        SELECT run_id, run_source FROM ops.pipeline_runs
        WHERE pipeline_name = %s AND (%s::text IS NULL OR run_id = %s)
        ORDER BY start_ts DESC LIMIT 1
        """,
        (name, run_id, run_id),
    )
    if run is None:
        raise ApiError(404, "not_found", f"Run '{run_id}' not found for pipeline '{name}'")
    logs = _logs(db, run["run_id"])
    note = None
    if not logs and run["run_source"] == "simulated_history":
        note = "Simulated history has no log lines; see the run's error_message."
    return schemas.RunLogs(
        run_id=run["run_id"], logs=[schemas.LogLine(**r) for r in logs], note=note
    )


@router.get("/runs/{run_id}", response_model=schemas.RunDetail)
def run_detail(run_id: str, db: DbDep) -> schemas.RunDetail:
    run = db.fetch_one(f"SELECT {_RUN_COLUMNS} FROM ops.pipeline_runs WHERE run_id = %s", (run_id,))  # noqa: S608
    if run is None:
        raise ApiError(404, "not_found", f"Run '{run_id}' not found")
    incidents = db.fetch_all(
        """
        SELECT * FROM ops.incidents
        WHERE run_id = %(run)s
           OR (pipeline_name = %(pipeline)s AND opened_at::date = %(day)s)
        ORDER BY opened_at
        """,
        {"run": run_id, "pipeline": run["pipeline_name"], "day": run["start_ts"].date()},
    )
    return schemas.RunDetail(
        run=schemas.Run(**run),
        logs=[schemas.LogLine(**r) for r in _logs(db, run_id)],
        incidents=[schemas.Incident(**i) for i in incidents],
    )


@router.post(
    "/pipelines/{name}/retry",
    response_model=schemas.RetryAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"description": "Pipeline cannot be run by the local job runner"},
        404: {"description": "Unknown pipeline or run"},
        409: {"description": "Runner disabled, or the pipeline is already running"},
    },
)
def retry_pipeline(
    name: str, db: DbDep, runner: RunnerDep, body: schemas.RetryRequest | None = None
) -> schemas.RetryAccepted:
    """Start a real re-run of a whitelisted local pipeline in the background.

    Returns 202 immediately; poll GET /pipelines/{name} to see the new run (trigger = retry).
    """
    if not runner.enabled:
        raise ApiError(409, "runner_disabled",
                       "Retries are disabled. Set RETRY_RUNNER_ENABLED=true to allow the API to run "
                       "local pipelines.")  # fmt: skip
    if not runner.can_run(name):
        _require_pipeline(db, name)  # unknown name -> 404; known but not local -> 400 below
        raise ApiError(400, "not_retryable",
                       f"'{name}' is not executed by the local job runner. Lakehouse pipelines run "
                       "with `docker compose run --rm spark python -m rideops.spark ...` or as "
                       "Databricks jobs.")  # fmt: skip
    run_id = body.run_id if body else None
    if run_id and not db.scalar(
        "SELECT 1 FROM ops.pipeline_runs WHERE run_id = %s AND pipeline_name = %s", (run_id, name)
    ):
        raise ApiError(404, "not_found", f"Run '{run_id}' not found for pipeline '{name}'")
    if run_id is None:
        run_id = db.scalar(
            "SELECT run_id FROM ops.pipeline_runs WHERE pipeline_name = %s ORDER BY start_ts DESC LIMIT 1",
            (name,),
        )
    if runner.is_active(name) or db.scalar(
        "SELECT 1 FROM ops.pipeline_runs WHERE pipeline_name = %s AND status = 'RUNNING' "
        "AND start_ts > now() - interval '2 hours'",
        (name,),
    ):
        raise ApiError(409, "already_running", f"'{name}' is already running")
    try:
        runner.submit(name, run_id)
    except AlreadyRunningError as exc:
        raise ApiError(409, "already_running", f"'{name}' is already running") from exc
    return schemas.RetryAccepted(
        accepted=True,
        pipeline_name=name,
        retry_of_run_id=run_id,
        message=f"Retry started. Poll GET /api/v1/pipelines/{name} for the new run.",
    )
