import { formatCompact } from "../../lib/format";
import type { ChartSpec } from "../types";

// Small, dependency-free charts for profiles and widgets. Every chart has its numbers in text
// (title attributes + visible labels), so nothing depends on colour or hover alone.

export function BarList({ bars, label }: { bars: [unknown, number][]; label: string }) {
  const max = Math.max(1, ...bars.map(([, n]) => n));
  return (
    <ul className="mini-bars" aria-label={label}>
      {bars.map(([value, n], i) => (
        <li key={i}>
          <span className="mini-bar-label" title={String(value)}>{value === null ? "null" : String(value)}</span>
          <span className="mini-bar-track"><span className="mini-bar-fill" style={{ width: `${(100 * n) / max}%` }} /></span>
          <span className="mini-bar-value mono">{formatCompact(n)}</span>
        </li>
      ))}
    </ul>
  );
}

export function Histogram({ bins, label }: { bins: [number, number, number][]; label: string }) {
  const max = Math.max(1, ...bins.map((b) => b[2]));
  return (
    <div className="histogram" role="img" aria-label={`${label}: ${bins.length} bins from ${bins[0]?.[0]} to ${bins.at(-1)?.[1]}`}>
      {bins.map(([lo, hi, n]) => (
        <span key={lo} className="histogram-bar" style={{ height: `${Math.max(2, (100 * n) / max)}%` }}
              title={`${lo} – ${hi}: ${n}`} />
      ))}
      <span className="histogram-axis mono">{formatCompact(bins[0]?.[0])}</span>
      <span className="histogram-axis right mono">{formatCompact(bins.at(-1)?.[1])}</span>
    </div>
  );
}

export function TimeBars({ points, label }: { points: [string, number][]; label: string }) {
  const max = Math.max(1, ...points.map(([, n]) => n));
  return (
    <div className="histogram" role="img" aria-label={`${label}: ${points.length} days`}>
      {points.map(([day, n]) => (
        <span key={day} className="histogram-bar" style={{ height: `${Math.max(2, (100 * n) / max)}%` }} title={`${day}: ${n}`} />
      ))}
      <span className="histogram-axis mono">{points[0]?.[0]}</span>
      <span className="histogram-axis right mono">{points.at(-1)?.[0]}</span>
    </div>
  );
}

export function AutoChart({ spec }: { spec: ChartSpec }) {
  switch (spec.type) {
    case "bar":
      return <BarList bars={spec.bars ?? []} label={spec.title} />;
    case "histogram":
      return <Histogram bins={spec.bins ?? []} label={spec.title} />;
    case "timeseries":
      return <TimeBars points={spec.points ?? []} label={spec.title} />;
    case "patterns":
      return <BarList bars={Object.entries(spec.patterns ?? {}).map(([k, v]) => [k, Math.round(v * 100)])} label={spec.title} />;
    default:
      return <p className="muted">{spec.note ?? "Statistics only"}</p>;
  }
}
