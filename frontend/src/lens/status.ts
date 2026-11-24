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
