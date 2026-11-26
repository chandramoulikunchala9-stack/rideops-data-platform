import type { IconName } from "../components/Icon";
import type { Tone } from "../lib/status";
import type { ConnState } from "./types";

// State is always icon + text + colour, never colour alone (wireframes legend).
export const STATE_META: Record<ConnState, { label: string; tone: Tone; icon: IconName }> = {
  healthy: { label: "Healthy", tone: "good", icon: "check" },
  degraded: { label: "Degraded", tone: "warning", icon: "warning" },
  down: { label: "Down", tone: "critical", icon: "x" },
  unreachable: { label: "Unreachable", tone: "critical", icon: "x" },
  auth_failed: { label: "Auth failed", tone: "critical", icon: "ban" },
  tls_error: { label: "TLS error", tone: "critical", icon: "ban" },
  unknown: { label: "Unknown", tone: "neutral", icon: "clock" },
  paused: { label: "Paused", tone: "neutral", icon: "clock" },
};

// Worst first, so problems sit at the top of the fleet table.
export const STATE_ORDER: ConnState[] = [
  "down", "unreachable", "auth_failed", "tls_error", "degraded", "unknown", "healthy", "paused",
];

export const ENGINE_LABEL: Record<string, string> = {
  postgres: "PostgreSQL", mysql: "MySQL", mssql: "SQL Server", sqlite: "SQLite", mongodb: "MongoDB",
};

export const ENV_LABEL: Record<string, string> = { prod: "Production", staging: "Staging", dev: "Development", demo: "Demo" };

export const engineLabel = (engine: string) => ENGINE_LABEL[engine] ?? engine;

// Where each engine's health figures are read from: shown as the lineage line on status sections
// (Product principle 2: every figure says where and when it was read).
export const ENGINE_SOURCES: Record<string, { health: string[]; activity: string[] }> = {
  postgres: { health: ["pg_stat_database", "pg_database_size()"], activity: ["pg_stat_activity", "pg_locks"] },
  mysql: { health: ["SHOW GLOBAL STATUS", "information_schema.TABLES"], activity: ["performance_schema", "processlist"] },
  mssql: { health: ["sys.dm_os_performance_counters", "sys.database_files"], activity: ["sys.dm_exec_sessions", "sys.dm_exec_requests"] },
  mongodb: { health: ["serverStatus", "dbStats"], activity: ["currentOp"] },
  sqlite: { health: ["PRAGMA page_count", "sqlite_master"], activity: [] },
};

// Which engine capability a status figure needs (backend adapters/base.py Capability).
export const METRIC_CAPABILITY: Record<string, string> = {
  sessions: "sessions",
  cache: "cache_hit",
  replication: "replication",
  long_queries: "long_queries",
  locks: "locks_blocking",
};

/** "3 d 2 h", "18 min", "40 s": a span of seconds, coarse enough to read at a glance. */
export function formatSpan(seconds: number | null | undefined): string {
  if (seconds == null) return "–";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ${m % 60} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

/** How long something has been true, from the moment it became true. */
export function formatSince(iso: string | null | undefined, now: number = Date.now()): string | null {
  if (!iso) return null;
  return formatSpan((now - new Date(iso).getTime()) / 1000);
}


export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
}

export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
