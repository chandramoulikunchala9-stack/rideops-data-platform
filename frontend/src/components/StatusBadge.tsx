import { type Tone, TONES } from "../lib/status";
import { Icon, type IconName } from "./Icon";

export function StatusBadge({ status }: { status: string }) {
  const meta = TONES[status] ?? { tone: "neutral" as Tone, icon: "clock" as IconName };
  return (
    <span className="badge" data-tone={meta.tone} data-status={status}>
      <Icon name={meta.icon} size={13} />
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
        Simulated
      </span>
    );
  }
  return <span className="badge">{sources.join(" + ")}</span>;
}
