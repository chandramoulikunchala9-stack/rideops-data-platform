import type { IconName } from "../components/Icon";

// Status colours are reserved for state and never appear alone: every badge has an icon + label.

export type Tone = "good" | "warning" | "serious" | "critical" | "neutral";

export const TONES: Record<string, { tone: Tone; icon: IconName; label?: string }> = {
  SUCCESS: { tone: "good", icon: "check", label: "Success" },
  PASS: { tone: "good", icon: "check", label: "Pass" },
  OK: { tone: "good", icon: "check", label: "On budget" },
  RESOLVED: { tone: "good", icon: "check", label: "Resolved" },
  RUNNING: { tone: "neutral", icon: "running", label: "Running" },
  WARN: { tone: "warning", icon: "warning", label: "Warn" },
  WARNING: { tone: "warning", icon: "warning", label: "Warning" },
  FORECAST_OVER_BUDGET: { tone: "warning", icon: "warning", label: "Forecast over budget" },
  CANCELLED: { tone: "serious", icon: "ban", label: "Cancelled" },
  FAILED: { tone: "critical", icon: "x", label: "Failed" },
  FAIL: { tone: "critical", icon: "x", label: "Fail" },
  CRITICAL: { tone: "critical", icon: "x", label: "Critical" },
  OVER_BUDGET: { tone: "critical", icon: "x", label: "Over budget" },
  OPEN: { tone: "critical", icon: "warning", label: "Open" },
  INVESTIGATING: { tone: "warning", icon: "clock", label: "Investigating" },
  // source-application health (GET /sources)
  ok: { tone: "good", icon: "check", label: "Healthy" },
  warn: { tone: "warning", icon: "warning", label: "Rows quarantined" },
  fail: { tone: "critical", icon: "x", label: "File failed" },
  empty: { tone: "neutral", icon: "clock", label: "No data yet" },
};

export function toneOf(status: string): Tone {
  return TONES[status]?.tone ?? "neutral";
}

