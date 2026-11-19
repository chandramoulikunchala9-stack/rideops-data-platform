"""Response models. They document the API (OpenAPI at /docs) and validate what is returned."""

from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(Model, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class Period(Model):
    start: date
    end: date


# ------------------------------------------------------------------ health
class Health(Model):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    version: str
    environment: str
    retry_runner_enabled: bool
    time: datetime


# ------------------------------------------------------------------ analytics
class RunBrief(Model):
    run_id: str
    pipeline_name: str
    status: str
    start_ts: datetime
    error_message: str | None = None


class CloudSpend(Model):
    month: str | None
    amount_usd: float
    sources: list[str] = Field(description="'simulated' unless real billing data was loaded")


class Overview(Model):
    period: Period
    data_as_of: date | None
    total_bookings: int
    completed_rides: int
    total_revenue_inr: float
    cancellation_rate: float
    active_drivers: int
    active_customers: int
    pipeline_success_rate: float | None
    recent_failures: list[RunBrief]
    cloud_spend: CloudSpend


class RevenuePoint(Model):
    period: date
    city: str
    revenue_inr: float
    bookings: int


class CityRevenue(Model):
    city: str
    city_name: str
    revenue_inr: float
    bookings: int
    avg_fare_inr: float | None


class Revenue(Model):
    period: Period
    granularity: str
    series: list[RevenuePoint]
    by_city: list[CityRevenue]


class DailyBookings(Model):
    date: date
    total: int
    completed: int
    cancelled: int
    no_driver_found: int
    cancellation_rate: float


class Bookings(Model):
    period: Period
    daily: list[DailyBookings]


class Driver(Model):
    driver_id: str
    full_name: str | None
    city: str | None
    rating: float | None
    total_assigned: int
    completed_rides: int
    driver_cancellations: int
    completion_rate: float
    total_revenue: float
    active_days: int
    revenue_rank_in_city: int


class CancellationReason(Model):
    cancelled_by: str
    cancellation_reason: str
    cancellations: int
    fees_collected: float


class Cancellations(Model):
    period: Period
    by_reason: list[CancellationReason]
    daily_rate: list[DailyBookings]


class PaymentMethodStats(Model):
    method: str
    attempts: int
    failed_attempts: int
    failure_rate: float
    charged_amount: float
    refunded_amount: float


class Payments(Model):
    period: Period
    by_method: list[PaymentMethodStats]


# ------------------------------------------------------------------ pipelines
class PipelineSummary(Model):
    pipeline_name: str
    layer: str
    last_run_id: str
    last_status: str
    last_run_at: datetime
    last_duration_sec: int | None
    last_error: str | None
    runs: int
    success_rate: float | None
    last_success_at: datetime | None
    p50_duration_sec: float | None
    run_sources: list[str]
    retryable: bool


class Run(Model):
    run_id: str
    pipeline_name: str
    layer: str
    trigger: str
    status: str
    start_ts: datetime
    end_ts: datetime | None
    duration_sec: int | None
    records_read: int | None
    records_written: int | None
    records_rejected: int | None
    retry_count: int
    error_message: str | None
    run_source: str
    retry_of_run_id: str | None = None


class LogLine(Model):
    logged_at: datetime
    level: str
    message: str


class RunLogs(Model):
    run_id: str
    logs: list[LogLine]
    note: str | None = None


class Incident(Model):
    incident_id: str
    title: str
    severity: str
    status: str
    pipeline_name: str | None
    run_id: str | None
    opened_at: datetime
    resolved_at: datetime | None
    root_cause: str | None = Field(None, description="where the incident came from")
    resolution: str | None = Field(None, description="Markdown notes: impact, timeline, fix")
    runbook: str | None


class RunDetail(Model):
    run: Run
    logs: list[LogLine]
    incidents: list[Incident]


class RetryRequest(Model):
    run_id: str | None = Field(None, description="Failed run to retry; defaults to the latest run")


class RetryAccepted(Model):
    accepted: bool
    pipeline_name: str
    retry_of_run_id: str | None
    message: str


# ------------------------------------------------------------------ monitoring
class Alert(Model):
    alert_id: int
    rule: str
    severity: str
    subject: str
    message: str
    raised_at: datetime
    acknowledged: bool


class MonitoringSummary(Model):
    runs_last_24h: dict[str, int]
    pipelines_by_latest_status: dict[str, int]
    failing_pipelines: list[str]
    long_running_runs_30d: int
    last_ingestion_at: datetime | None
    hours_since_ingestion: float | None
    data_quality: dict[str, int]
    open_incidents: int
    active_alerts: dict[str, int]


# ------------------------------------------------------------------ costs
class CostBreakdown(Model):
    name: str
    cost_usd: float
    share: float


class ExpensiveJob(Model):
    pipeline_name: str
    cost_usd: float
    runs: int
    cost_per_run_usd: float | None


class CostSummary(Model):
    month: str
    sources: list[str]
    total_usd: float
    as_of: date
    forecast_usd: float
    budget_usd: float
    budget_status: Literal["OK", "FORECAST_OVER_BUDGET", "OVER_BUDGET"]
    by_service: list[CostBreakdown]
    by_workload: list[CostBreakdown]
    by_environment: list[CostBreakdown]
    untagged_usd: float
    expensive_jobs: list[ExpensiveJob]


class CostPoint(Model):
    date: date
    group: str
    cost_usd: float


class CostAnomaly(Model):
    date: date
    cost_usd: float
    trailing_mean_usd: float
    z_score: float


class CostTrends(Model):
    period: Period
    group_by: str
    sources: list[str]
    series: list[CostPoint]
    daily_totals: list[CostPoint]
    anomalies: list[CostAnomaly]


# ------------------------------------------------------------------ data quality
class CheckResult(Model):
    check_name: str
    entity: str
    check_type: str
    status: str
    failed_rows: int
    total_rows: int
    failure_rate: float
    threshold: float
    details: dict | list | None
    checked_at: datetime


class Rejection(Model):
    entity: str
    reason: str
    rows: int


class DataQuality(Model):
    status_counts: dict[str, int]
    checks: list[CheckResult]
    rejections: list[Rejection]
    last_checked_at: datetime | None


# ------------------------------------------------------------------ access
class Role(Model):
    name: str
    group: str
    description: str
    permissions: list[str]


class DemoUser(Model):
    name: str
    email: str
    role: str


class AccessSettings(Model):
    roles: list[Role]
    users: list[DemoUser]
    users_note: str
    environment: dict[str, str | bool | float | None]
