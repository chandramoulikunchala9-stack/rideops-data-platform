// TypeScript mirrors of the backend response models (backend/src/rideops_api/schemas.py).
// Keeping them explicit means a backend change shows up as a type error, not a blank chart.

export interface Period {
  start: string;
  end: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Health {
  status: "ok" | "degraded";
  database: "ok" | "unavailable";
  version: string;
  environment: string;
  retry_runner_enabled: boolean;
  time: string;
}

export interface RunBrief {
  run_id: string;
  pipeline_name: string;
  status: RunStatus;
  start_ts: string;
  error_message: string | null;
}

export interface Overview {
  period: Period;
  data_as_of: string | null;
  total_bookings: number;
  completed_rides: number;
  total_revenue_inr: number;
  cancellation_rate: number;
  active_drivers: number;
  active_customers: number;
  pipeline_success_rate: number | null;
  failed_runs_30d: number;
  recent_failures: RunBrief[];
  cloud_spend: { month: string | null; amount_usd: number; sources: string[] };
}

export interface RevenuePoint {
  period: string;
  city: string;
  revenue_inr: number;
  bookings: number;
}

export interface Revenue {
  period: Period;
  granularity: "day" | "week" | "month";
  series: RevenuePoint[];
  by_city: { city: string; city_name: string; revenue_inr: number; bookings: number; avg_fare_inr: number | null }[];
}

export interface DailyBookings {
  date: string;
  total: number;
  completed: number;
  cancelled: number;
  no_driver_found: number;
  cancellation_rate: number;
}

export interface Bookings {
  period: Period;
  daily: DailyBookings[];
}

export interface Driver {
  driver_id: string;
  full_name: string | null;
  city: string | null;
  rating: number | null;
  total_assigned: number;
  completed_rides: number;
  driver_cancellations: number;
  completion_rate: number;
  total_revenue: number;
  active_days: number;
  revenue_rank_in_city: number;
}

export interface Cancellations {
  period: Period;
  by_reason: { cancelled_by: string; cancellation_reason: string; cancellations: number; fees_collected: number }[];
  daily_rate: DailyBookings[];
}

export interface Payments {
  period: Period;
  by_method: {
    method: string;
    attempts: number;
    failed_attempts: number;
    failure_rate: number;
    charged_amount: number;
    refunded_amount: number;
  }[];
}

export type RunStatus = "RUNNING" | "SUCCESS" | "FAILED" | "CANCELLED";

export interface PipelineSummary {
  pipeline_name: string;
  layer: string;
  last_run_id: string;
  last_status: RunStatus;
  last_run_at: string;
  last_duration_sec: number | null;
  last_error: string | null;
  runs: number;
  success_rate: number | null;
  last_success_at: string | null;
  p50_duration_sec: number | null;
  run_sources: string[];
  retryable: boolean;
}

export interface Run {
  run_id: string;
  pipeline_name: string;
  layer: string;
  trigger: string;
  status: RunStatus;
  start_ts: string;
  end_ts: string | null;
  duration_sec: number | null;
  records_read: number | null;
  records_written: number | null;
  records_rejected: number | null;
  retry_count: number;
  error_message: string | null;
  run_source: string;
  retry_of_run_id: string | null;
}

export interface LogLine {
  logged_at: string;
  level: string;
  message: string;
}

export interface Incident {
  incident_id: string;
  title: string;
  severity: string;
  status: string;
  pipeline_name: string | null;
  run_id: string | null;
  opened_at: string;
  resolved_at: string | null;
  root_cause: string | null;
  resolution: string | null;
  runbook: string | null;
}

export interface RunDetail {
  run: Run;
  logs: LogLine[];
  incidents: Incident[];
}

export interface RetryAccepted {
  accepted: boolean;
  pipeline_name: string;
  retry_of_run_id: string | null;
  message: string;
}

export interface Alert {
  alert_id: number;
  rule: string;
  severity: "INFO" | "WARNING" | "CRITICAL";
  subject: string;
  message: string;
  raised_at: string;
  acknowledged: boolean;
}

export interface MonitoringSummary {
  runs_last_24h: Record<string, number>;
  pipelines_by_latest_status: Record<string, number>;
  failing_pipelines: string[];
  long_running_runs_30d: number;
  last_ingestion_at: string | null;
  hours_since_ingestion: number | null;
  data_quality: Record<string, number>;
  open_incidents: number;
  active_alerts: Record<string, number>;
}

export interface CostBreakdown {
  name: string;
  cost_usd: number;
  share: number;
}

export interface CostSummary {
  month: string;
  sources: string[];
  total_usd: number;
  as_of: string;
  forecast_usd: number;
  budget_usd: number;
  budget_status: "OK" | "FORECAST_OVER_BUDGET" | "OVER_BUDGET";
  by_service: CostBreakdown[];
  by_workload: CostBreakdown[];
  by_environment: CostBreakdown[];
  untagged_usd: number;
  expensive_jobs: { pipeline_name: string; cost_usd: number; runs: number; cost_per_run_usd: number | null }[];
}

export interface CostPoint {
  date: string;
  group: string;
  cost_usd: number;
}

export interface CostTrends {
  period: Period;
  group_by: string;
  sources: string[];
  series: CostPoint[];
  daily_totals: CostPoint[];
  anomalies: { date: string; cost_usd: number; trailing_mean_usd: number; z_score: number }[];
}

export type CheckStatus = "PASS" | "WARN" | "FAIL";

export interface CheckResult {
  check_name: string;
  entity: string;
  check_type: string;
  status: CheckStatus;
  failed_rows: number;
  total_rows: number;
  failure_rate: number;
  threshold: number;
  details: unknown;
  checked_at: string;
}

export interface DataQuality {
  status_counts: Record<CheckStatus, number>;
  checks: CheckResult[];
  rejections: { entity: string; reason: string; rows: number }[];
  last_checked_at: string | null;
}

export interface AccessSettings {
  roles: { name: string; group: string; description: string; permissions: string[] }[];
  users: { name: string; email: string; role: string }[];
  users_note: string;
  environment: Record<string, string | number | boolean | null>;
}

export interface SourceEntity {
  entity: string;
  files: number;
  failed_files: number;
  rows_loaded: number;
  rows_current: number;
  rows_rejected: number;
  bytes: number;
  last_loaded_at: string | null;
}

export interface SourceApp {
  id: "rider_app" | "driver_app" | "payments" | "cloud_billing" | "platform_ops";
  name: string;
  description: string;
  status: "ok" | "warn" | "fail" | "empty";
  rows_loaded: number;
  rows_rejected: number;
  last_loaded_at: string | null;
  entities: SourceEntity[];
}

export interface Sources {
  sources: SourceApp[];
}

export interface AppPlatformStats {
  platform: string;
  sessions: number;
  fare_views: number;
  bookings: number;
  crashes: number;
  conversion_rate: number | null;
}

export interface AppFunnel {
  available: boolean;
  period: Period;
  platforms: AppPlatformStats[];
  daily: { date: string; platform: string; sessions: number; bookings: number }[];
}
