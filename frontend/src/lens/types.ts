// Types for the Lens API (backend: rideops_api/auth, workspaces, lens/connections).

export type Role = "owner" | "admin" | "editor" | "viewer";

export interface Membership {
  workspace_id: string;
  name: string;
  slug: string;
  role: Role;
}

export interface Me {
  user: { id: string; email: string; display_name: string; email_verified: boolean };
  memberships: Membership[];
  current_workspace_id: string | null;
  csrf_token: string;
}

export interface WorkspaceSettings {
  allow_insecure_connections: boolean;
  allowed_ports: number[];
  egress_allowlist: string[];
}

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  plan: string;
  settings: WorkspaceSettings;
  role: Role;
  created_at: string;
}

export type ConnState =
  | "healthy" | "degraded" | "down" | "auth_failed" | "tls_error" | "unreachable" | "unknown" | "paused";

export interface Connection {
  id: string;
  name: string;
  engine: string;
  environment: "prod" | "staging" | "dev" | "demo";
  host: string | null;
  port: number | null;
  database: string | null;
  username: string | null;
  display_url: string;
  tls_mode: string;
  tags: string[];
  color: string | null;
  check_interval_s: number;
  is_paused: boolean;
  is_demo: boolean;
  current_state: ConnState;
  health_score: number | null;
  server_version: string | null;
  capabilities: string[];
  last_checked_at: string | null;
  last_state_change_at: string | null;
  created_at: string;
  deleted_at?: string | null;
  sparkline: (number | null)[];
}

export interface Stage {
  stage: string;
  ok: boolean;
  ms: number;
  message: string;
  code: string | null;
  warning: boolean;
}

export interface TestResult {
  test_id: string;
  ok: boolean;
  server_version: string | null;
  stages: Stage[];
  capabilities: string[];
  privilege_findings: { privilege: string; object: string; severity: string; tables?: number }[];
  grant_script: string | null;
}

export interface ParsedUrl {
  engine: string;
  host: string;
  port: number;
  database: string | null;
  username: string | null;
  options: Record<string, string>;
  tls_mode: string;
  has_password: boolean;
  display_url: string;
}

export interface Snapshot {
  taken_at: string;
  state: ConnState;
  reachable: boolean;
  latency_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  server_version: string | null;
  uptime_s: number | null;
  db_size_bytes: number | null;
  health_score: number | null;
  score_breakdown: { component: string; points: number; reason: string }[];
  metrics: Record<string, number | null>;
  details: { long_running?: { pid: number; user: string; runtime_s: number; query: string | null }[] };
  unavailable: Record<string, string>;
}

export interface ConnectionStatus {
  connection: Connection;
  latest: Snapshot | null;
  uptime_24h: number | null;
}

export interface HistoryPoint {
  at: string;
  state: ConnState | null;
  latency_ms: number | null;
  health_score: number | null;
  up_checks: number;
  total_checks: number;
}

export interface Uptime {
  window: string;
  uptime_pct: number | null;
  checks: number;
  bars: { start: string; uptime_pct: number | null; checks: number }[];
}

export interface FleetOverview {
  total: number;
  by_state: Partial<Record<ConnState, number>>;
  worst: Connection[];
  egress_ips: string[];
}

export interface EngineInfo {
  engine: string;
  label: string;
  default_port: number | null;
  release: string;
  example: string | null;
  available: boolean;
  capabilities: string[];
}

// ---- Phase 13: analytics ---------------------------------------------------

export interface CatalogTable {
  schema_name: string;
  table_name: string;
  kind: string;
  comment: string | null;
  primary_key: string[] | null;
  freshness_column: string | null;
  pii_columns: string[];
  is_pinned: boolean;
  column_count: number;
  row_estimate: number | null;
  total_bytes: number | null;
  freshness_max: string | null;
  matching_columns: string[];
}

export interface Catalog {
  last_sync: { started_at: string; finished_at: string | null; ok: boolean | null; error: string | null } | null;
  schemas: string[];
  tables: CatalogTable[];
  total_bytes: number;
}

export interface ColumnInfo {
  name: string;
  data_type: string;
  nullable: boolean;
  ordinal: number;
  default: string | null;
  comment: string | null;
  is_pk: boolean;
  pii: boolean;
}

export interface TableDetail {
  table: {
    schema_name: string; table_name: string; kind: string; comment: string | null; columns: ColumnInfo[];
    indexes: { name: string; columns: string[]; unique: boolean; primary: boolean; bytes: number; scans: number }[];
    foreign_keys: { name: string; columns: string[]; ref_table: string; ref_columns: string[] }[];
    primary_key: string[] | null; freshness_column: string | null; pii_columns: string[]; is_pinned: boolean;
  };
  stats: {
    taken_at: string; row_estimate: number | null; row_exact: number | null; total_bytes: number | null;
    table_bytes: number | null; index_bytes: number | null; dead_rows: number | null; seq_scans: number | null;
    idx_scans: number | null; last_vacuum: string | null; last_analyze: string | null; freshness_max: string | null;
  } | null;
  changes: SchemaChange[];
  referenced_by: { schema_name: string; table_name: string; fk: { columns: string[] } }[];
}

export interface SchemaChange {
  id?: number;
  detected_at: string;
  change_type: string;
  object_ref: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface ColumnProfile {
  column: string;
  data_type: string;
  kind: string;
  sample_size: number;
  null_pct: number | null;
  distinct_est: number | null;
  pii: boolean;
  min?: unknown;
  max?: unknown;
  mean?: number;
  stddev?: number;
  quantiles?: Record<string, number>;
  top_k?: [unknown, number][];
  length?: { min: number; max: number; mean: number };
  patterns?: Record<string, number>;
}

export interface ChartSpec {
  column: string;
  type: "bar" | "histogram" | "timeseries" | "patterns" | "stats";
  title: string;
  bars?: [unknown, number][];
  bins?: [number, number, number][];
  points?: [string, number][];
  patterns?: Record<string, number>;
  note?: string;
}

export interface ProfileResponse {
  profile: { profiled_at: string; sample_rows: number; method: string; elapsed_ms: number | null;
             profiles: ColumnProfile[]; charts: ChartSpec[] } | null;
  pending: boolean;
}

export interface Insight {
  id: number;
  code: string;
  severity: "info" | "warning" | "critical";
  object_ref: string;
  title: string;
  detail: Record<string, unknown>;
  recommendation: string | null;
  first_seen_at: string;
  last_seen_at: string;
}

export interface QueryResult {
  run_id: string;
  kind: string;
  columns: { name: string; type: string }[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
  masked_columns: string[];
}

export interface SavedQuery {
  id: string;
  connection_id: string;
  connection_name?: string;
  name: string;
  description: string | null;
  statement: string;
  tags: string[];
  updated_at: string;
}

export interface Widget {
  id: string;
  kind: "kpi" | "line" | "bar" | "table" | "text";
  title: string;
  position: number;
  source: { type: string; id?: string };
  options: { x?: string; y?: string; text?: string; format?: string };
  query_name: string | null;
  result_cache: { columns: { name: string }[]; rows: unknown[][]; row_count: number; truncated: boolean } | null;
  refreshed_at: string | null;
  last_status: string | null;
  last_error: string | null;
  stale: boolean;
}

export interface Dashboard {
  id: string;
  name: string;
  description: string | null;
  refresh_interval_s: number | null;
  last_refreshed_at: string | null;
  created_at: string;
  widget_count?: number;
  widgets?: Widget[];
}

// ---- Phase 14: alerts --------------------------------------------------------

export type Severity = "info" | "warning" | "critical";

export interface AlertEvent {
  id: number;
  status: "firing" | "acknowledged" | "resolved" | "muted";
  severity: Severity;
  summary: string;
  opened_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  muted_until: string | null;
  connection_id: string | null;
  connection_name: string | null;
  incident_id: string | null;
  rule_name: string;
  runbook_url: string | null;
}

export interface AlertRule {
  id: string;
  connection_id: string | null;
  name: string;
  rule_type: "status" | "metric" | "freshness" | "row_drop" | "schema_change" | "privilege";
  condition: Record<string, unknown>;
  severity: Severity;
  channels: string[];
  runbook_url: string | null;
  is_enabled: boolean;
  is_default: boolean;
}

export interface Channel {
  id: string;
  kind: "email" | "webhook" | "slack" | "inapp";
  name: string;
  summary: string | null;
  is_verified: boolean;
  created_at: string;
  signing_secret?: string;
}

export interface Incident {
  id: string;
  title: string;
  severity: Severity;
  status: "open" | "acknowledged" | "resolved";
  connection_id: string | null;
  connection_name?: string | null;
  runbook_url: string | null;
  opened_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  alert_count?: number;
  notes?: { id: number; kind: "note" | "event"; body: string; created_at: string; author: string | null }[];
  alerts?: { id: number; status: string; severity: Severity; summary: string; opened_at: string }[];
}

export interface NotificationList {
  unread: number;
  items: { id: number; kind: string; title: string; body: string | null; link: string | null; read_at: string | null; created_at: string }[];
}
