import type { Uptime } from "../types";

function toneFor(pct: number | null): string {
  if (pct == null) return "none";
  if (pct >= 99.5) return "good";
  if (pct >= 95) return "warning";
  return "critical";
}

/** One bar per bucket (hour for 24 h, day for 7 d / 30 d). Hover/focus shows the value. */
export function UptimeBars({ uptime }: { uptime: Uptime }) {
  if (!uptime.bars.length) return <p className="muted">No checks in this window yet.</p>;
  return (
    <div className="uptime-bars" role="list" aria-label={`Uptime ${uptime.window}`}>
      {uptime.bars.map((bar) => {
        const when = new Date(bar.start).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
        const text = `${when} UTC: ${bar.uptime_pct == null ? "no data" : `${bar.uptime_pct}%`} (${bar.checks} checks)`;
        return <span key={bar.start} role="listitem" className="uptime-bar" data-tone={toneFor(bar.uptime_pct)} title={text} aria-label={text} />;
      })}
    </div>
  );
}
