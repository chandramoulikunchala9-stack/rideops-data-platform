"""Operational datasets: pipeline execution history and cloud usage/cost records.

Both are SIMULATED and labelled so in every row:
  pipeline_runs.run_source = "simulated_history"  (real runs from later phases use "local_run" etc.)
  cloud_costs.source       = "simulated"          (real AWS CUR / Databricks data use other values)

Pipeline runs follow the dependency graph in reference.PIPELINES. One deliberate incident is
included (see INCIDENT_*), so monitoring and runbooks have something real to investigate.

Costs are *derived from the runs*: a pipeline that runs longer on a bigger cluster costs more.
Unit prices are illustrative round numbers, not quotes from AWS or Databricks.
"""

import random
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from rideops.generators.common import EntityBatch, make_rng, parse_ts, to_iso
from rideops.generators.defects import DefectRates, Record, apply_defects
from rideops.generators.reference import PIPELINES, PipelineSpec

RUN_FIELDS = (
    "run_id", "pipeline_name", "layer", "trigger", "status", "start_ts", "end_ts",
    "duration_sec", "records_read", "records_written", "records_rejected", "retry_count",
    "error_message", "run_source",
)  # fmt: skip
COST_FIELDS = (
    "cost_id", "usage_date", "provider", "service", "workload", "environment", "usage_type",
    "usage_quantity", "usage_unit", "unit_price_usd", "cost_usd", "currency", "source",
)  # fmt: skip

P_FAIL = 0.04
P_RETRY_SUCCEEDS = 0.8
P_LONG_RUNNING = 0.02

# Typical failure messages, each mapped to a runbook in monitoring/runbooks/.
ERRORS: dict[str, str] = {
    "missing_source_file": "FileNotFoundException: No files found under "
    "landing/{entity}/batch_date={day}/ - expected at least 1",
    "permission_denied": "AccessDeniedException: User arn:aws:sts::000000000000:assumed-role/"
    "rideops-pipeline is not authorized to perform s3:GetObject",
    "insufficient_compute": "SparkException: Job aborted due to stage failure: ExecutorLostFailure "
    "(executor 3 exited) Reason: Container killed for exceeding memory limits",
    "delayed_ingestion": "TimeoutException: upstream data for {day} not available after 60 minutes",
    "duplicate_data": "DataQualityException: 1,024 duplicate booking_id values in silver.bookings",
    "checkpoint_failure": "StreamingQueryException: checkpoint at checkpoints/ride_events is "
    "incompatible with the current query (offsets log version mismatch)",
}
_ERROR_BY_LAYER = {
    "bronze": ("missing_source_file", "permission_denied", "delayed_ingestion"),
    "silver": ("insufficient_compute", "duplicate_data"),
    "gold": ("insufficient_compute",),
    "serving": ("permission_denied",),
    "ops": ("missing_source_file",),
}

# The deliberate incident: a source system changed `amount` from number to string.
INCIDENT_DAYS_BEFORE_END = 12
INCIDENT_PIPELINE = "ingest_payments"
INCIDENT_ERROR = (
    "AnalysisException: [DELTA_FAILED_TO_MERGE_FIELDS] Failed to merge fields 'amount' "
    "and 'amount'. Failed to merge incompatible data types DOUBLE and STRING"
)

# Illustrative unit prices (USD).
DBU_PRICE_JOBS = 0.15
DBU_PRICE_SQL = 0.22
DBU_PER_WORKER_HOUR = 1.0
EC2_PRICE_PER_WORKER_HOUR = 0.25
S3_PRICE_PER_GB_MONTH = 0.023
TRANSFER_PRICE_PER_GB = 0.09
RUNAWAY_DAYS_BEFORE_END = (30, 29)  # a dev cluster left running over a weekend


# --------------------------------------------------------------------------- pipeline runs


def _daily_counts(bookings: list[Record]) -> Counter:
    return Counter(b["requested_at"][:10] for b in bookings)


def _run(
    rng: random.Random,
    spec: PipelineSpec,
    start: datetime,
    *,
    trigger: str,
    retry_count: int,
    records: int,
    fail_error: str | None,
    day: date,
) -> Record:
    minutes = spec.baseline_minutes * rng.lognormvariate(0, 0.2) * (records / 1000 + 0.5) ** 0.3
    if fail_error is None and rng.random() < P_LONG_RUNNING:
        minutes *= rng.uniform(3, 5)  # long-running: skew, slow storage, noisy neighbour
    if fail_error is not None:
        minutes *= rng.uniform(0.1, 0.6)  # failures usually stop early
    end = start + timedelta(minutes=minutes)
    rejected = 0 if fail_error else int(records * rng.uniform(0.002, 0.02))
    return {
        "run_id": f"RUN-{day:%Y%m%d}-{spec.name}-{retry_count}",
        "pipeline_name": spec.name,
        "layer": spec.layer,
        "trigger": trigger,
        "status": "FAILED" if fail_error else "SUCCESS",
        "start_ts": to_iso(start),
        "end_ts": to_iso(end),
        "duration_sec": int((end - start).total_seconds()),
        "records_read": records,
        "records_written": 0 if fail_error else records - rejected,
        "records_rejected": rejected,
        "retry_count": retry_count,
        "error_message": fail_error,
        "run_source": "simulated_history",
    }


def build_pipeline_runs(
    bookings: list[Record], *, seed: int, end_date: date, history_days: int
) -> list[Record]:
    rng = make_rng(seed, "pipeline_runs")
    per_day = _daily_counts(bookings)
    incident_day = end_date - timedelta(days=INCIDENT_DAYS_BEFORE_END)
    runs: list[Record] = []

    for offset in range(history_days, 0, -1):  # each run processes the previous day's data
        day = end_date - timedelta(days=offset - 1)
        data_day = day - timedelta(days=1)
        volume = per_day.get(data_day.isoformat(), 0) or 500
        finished: dict[str, tuple[str, datetime]] = {}  # pipeline -> (final status, end time)

        for spec in PIPELINES:
            upstream = [finished[d] for d in spec.depends_on]
            if any(status != "SUCCESS" for status, _ in upstream):
                start = max(t for _, t in upstream)
                runs.append({
                    **dict.fromkeys(RUN_FIELDS),
                    "run_id": f"RUN-{day:%Y%m%d}-{spec.name}-0", "pipeline_name": spec.name,
                    "layer": spec.layer, "trigger": "scheduled", "status": "CANCELLED",
                    "start_ts": to_iso(start), "end_ts": to_iso(start), "duration_sec": 0,
                    "records_read": 0, "records_written": 0, "records_rejected": 0,
                    "retry_count": 0, "error_message": "Cancelled: upstream dependency failed",
                    "run_source": "simulated_history",
                })  # fmt: skip
                finished[spec.name] = ("CANCELLED", start)
                continue

            start = datetime.combine(day, time(spec.schedule_hour_utc), tzinfo=timezone.utc)
            if upstream:
                start = max(start, max(t for _, t in upstream))
            start += timedelta(seconds=rng.randint(5, 240))
            records = int(volume * (1 + rng.uniform(-0.05, 0.05)))
            if spec.name == INCIDENT_PIPELINE and day == incident_day + timedelta(days=1):
                records *= 2  # backfill after the incident

            for attempt in range(3):  # first run + up to two retries
                if spec.name == INCIDENT_PIPELINE and day == incident_day:
                    error = INCIDENT_ERROR
                elif rng.random() < (P_FAIL if attempt == 0 else 1 - P_RETRY_SUCCEEDS):
                    key = rng.choice(_ERROR_BY_LAYER[spec.layer])
                    error = ERRORS[key].format(
                        entity=spec.name.removeprefix("ingest_"), day=data_day
                    )
                else:
                    error = None
                run = _run(
                    rng,
                    spec,
                    start,
                    trigger="scheduled" if attempt == 0 else "retry",
                    retry_count=attempt,
                    records=records,
                    fail_error=error,
                    day=day,
                )
                runs.append(run)
                end = parse_ts(run["end_ts"])
                if error is None:
                    finished[spec.name] = ("SUCCESS", end)
                    break
                start = end + timedelta(minutes=rng.uniform(5, 15))  # automatic retry delay
            else:
                finished[spec.name] = ("FAILED", end)

    # The most recent export is still running at the moment the history was captured.
    last = next(r for r in reversed(runs) if r["pipeline_name"] == "export_to_postgres")
    if last["status"] == "SUCCESS":
        last.update(status="RUNNING", end_ts=None, duration_sec=None, records_written=None)
    return runs


def _corrupt_run(record: Record, rng: random.Random) -> None:
    if record["end_ts"]:  # clock skew: ends before it starts
        record["start_ts"], record["end_ts"] = record["end_ts"], record["start_ts"]
    else:
        record["status"] = "running"  # wrong case


# --------------------------------------------------------------------------- cloud costs


def build_cloud_costs(
    runs: list[Record], *, seed: int, end_date: date, history_days: int
) -> list[Record]:
    rng = make_rng(seed, "cloud_costs")
    worker_hours: dict[tuple[str, str], float] = {}
    workers = {p.name: p.workers for p in PIPELINES}
    for r in runs:
        if r["duration_sec"]:
            key = (r["start_ts"][:10], r["pipeline_name"])
            worker_hours[key] = worker_hours.get(key, 0) + r["duration_sec"] / 3600 * (
                workers[r["pipeline_name"]] + 1  # + driver node
            )

    costs: list[Record] = []

    def add(day: date, provider: str, service: str, workload: str | None, env: str,
            usage_type: str, qty: float, unit: str, price: float) -> None:  # fmt: skip
        costs.append({
            "cost_id": f"CST-{len(costs) + 1:07d}", "usage_date": day.isoformat(),
            "provider": provider, "service": service, "workload": workload, "environment": env,
            "usage_type": usage_type, "usage_quantity": round(qty, 4), "usage_unit": unit,
            "unit_price_usd": price, "cost_usd": round(qty * price, 4), "currency": "USD",
            "source": "simulated",
        })  # fmt: skip

    runaway = {end_date - timedelta(days=d) for d in RUNAWAY_DAYS_BEFORE_END}
    for offset in range(history_days - 1, -1, -1):
        day = end_date - timedelta(days=offset)
        weekday = day.weekday() < 5
        for spec in PIPELINES:
            hours = worker_hours.get((day.isoformat(), spec.name), 0)
            if hours:
                dbus = hours * DBU_PER_WORKER_HOUR
                add(day, "databricks", "Jobs Compute", spec.name, "prod", "DBU", dbus, "DBU",
                    DBU_PRICE_JOBS)  # fmt: skip
                add(day, "aws", "Amazon EC2", spec.name, "prod", "BoxUsage", hours, "Hrs",
                    EC2_PRICE_PER_WORKER_HOUR)  # fmt: skip

        sql_hours = rng.uniform(8, 11) if weekday else rng.uniform(2, 4)
        add(day, "databricks", "SQL Warehouse", "bi_dashboards", "prod", "DBU", sql_hours * 12,
            "DBU", DBU_PRICE_SQL)  # fmt: skip
        stored_gb = 500 + 8 * (history_days - offset)  # the lake grows every day
        add(day, "aws", "Amazon S3", "data_lake", "prod", "TimedStorage-ByteHrs",
            stored_gb / 30, "GB-Mo", S3_PRICE_PER_GB_MONTH)  # fmt: skip
        add(day, "aws", "AWS Data Transfer", "api_serving", "prod", "DataTransfer-Out-Bytes",
            rng.uniform(20, 60), "GB", TRANSFER_PRICE_PER_GB)  # fmt: skip

        dev_hours = (24 * 8) if day in runaway else (rng.uniform(4, 12) if weekday else 0.5)
        add(day, "databricks", "All-Purpose Compute", "development", "dev", "DBU", dev_hours,
            "DBU", 0.40)  # fmt: skip
        add(day, "aws", "Amazon EC2", "development", "dev", "BoxUsage", dev_hours, "Hrs",
            EC2_PRICE_PER_WORKER_HOUR)  # fmt: skip
    return costs


def _corrupt_cost(record: Record, rng: random.Random) -> None:
    record["cost_usd"] = round(record["cost_usd"] * 10, 4)  # no longer = quantity x price


# --------------------------------------------------------------------------- public API


def generate_ops(
    bookings: list[Record],
    *,
    seed: int,
    end_date: date,
    history_days: int,
    defects: DefectRates,
) -> tuple[EntityBatch, EntityBatch]:
    """Pipeline runs and cloud costs. Costs are derived from the *clean* runs."""
    runs = build_pipeline_runs(bookings, seed=seed, end_date=end_date, history_days=history_days)
    costs = build_cloud_costs(runs, seed=seed, end_date=end_date, history_days=history_days)

    run_counts = apply_defects(
        runs, make_rng(seed, "pipeline_runs-defects"), defects, corrupt=_corrupt_run,
        nullable_fields=("records_rejected",), mutate_duplicate=None,
    )  # fmt: skip
    cost_counts = apply_defects(
        costs, make_rng(seed, "cloud_costs-defects"), defects, corrupt=_corrupt_cost,
        nullable_fields=("workload",),  # untagged resources: a classic FinOps problem
        mutate_duplicate=None,  # billing files re-delivered
    )  # fmt: skip
    return (
        EntityBatch("pipeline_runs", runs, RUN_FIELDS, "jsonl", run_counts),
        EntityBatch("cloud_costs", costs, COST_FIELDS, "csv", cost_counts),
    )
