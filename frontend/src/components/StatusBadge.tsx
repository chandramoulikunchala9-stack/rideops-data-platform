// Status colours are reserved for state and never appear alone: every badge has an icon + label.

type Tone = "good" | "warning" | "serious" | "critical" | "neutral";

const TONES: Record<string, { tone: Tone; icon: string; label?: string }> = {
  SUCCESS: { tone: "good", icon: "✓", label: "Success" },
  PASS: { tone: "good", icon: "✓", label: "Pass" },
  OK: { tone: "good", icon: "✓", label: "On budget" },
  RESOLVED: { tone: "good", icon: "✓", label: "Resolved" },
  RUNNING: { tone: "neutral", icon: "●", label: "Running" },
  WARN: { tone: "warning", icon: "!", label: "Warn" },
  WARNING: { tone: "warning", icon: "!", label: "Warning" },
  FORECAST_OVER_BUDGET: { tone: "warning", icon: "!", label: "Forecast over budget" },
  CANCELLED: { tone: "serious", icon: "⊘", label: "Cancelled" },
  FAILED: { tone: "critical", icon: "✕", label: "Failed" },
  FAIL: { tone: "critical", icon: "✕", label: "Fail" },
  CRITICAL: { tone: "critical", icon: "✕", label: "Critical" },
  OVER_BUDGET: { tone: "critical", icon: "✕", label: "Over budget" },
  OPEN: { tone: "critical", icon: "!", label: "Open" },
  INVESTIGATING: { tone: "warning", icon: "…", label: "Investigating" },
};

const TONE_COLOR: Record<Tone, string> = {
  good: "var(--status-good)",
  warning: "var(--status-warning)",
  serious: "var(--status-serious)",
  critical: "var(--status-critical)",
  neutral: "var(--status-neutral)",
};

export function StatusBadge({ status }: { status: string }) {
  const meta = TONES[status] ?? { tone: "neutral" as Tone, icon: "•" };
  return (
    <span className="badge" data-status={status}>
      <span className="dot" style={{ background: TONE_COLOR[meta.tone] }} aria-hidden="true" />
      <span aria-hidden="true">{meta.icon}</span>
      {meta.label ?? status}
    </span>
  );
}

/** Marks figures that are not real measurements (e.g. simulated cloud costs). */
export function SourceBadge({ sources }: { sources: string[] }) {
  const simulated = sources.length === 0 || sources.every((s) => s === "simulated" || s === "simulated_history");
  if (simulated) {
    return (
      <span className="badge simulated" title="Synthetic data generated for this demo, not real spending">
        SIMULATED
      </span>
    );
  }
  return <span className="badge">{sources.join(" + ")}</span>;
}
