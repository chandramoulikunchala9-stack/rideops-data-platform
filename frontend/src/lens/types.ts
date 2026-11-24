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
