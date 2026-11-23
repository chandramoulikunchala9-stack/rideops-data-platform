import { useState, type ReactNode } from "react";

import { Card } from "./Card";

export interface LegendItem {
  label: string;
  color: string;
  shape?: "line" | "block";
}

interface ChartCardProps {
  title: string;
  subtitle?: ReactNode;
  legend?: LegendItem[];
  actions?: ReactNode;
  chart: ReactNode;
  table: ReactNode;
  /** Chart area height in px (default 280). */
  height?: number;
  /** HTML (non-Recharts) charts may grow past `height` when their rows wrap on small screens. */
  grow?: boolean;
}

/**
 * Every chart has a table view (accessibility + exact numbers) and, for ≥ 2 series, a legend,
 * so identity never depends on colour alone.
 */
export function ChartCard({ title, subtitle, legend, actions, chart, table, height = 280, grow = false }: ChartCardProps) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <Card
      title={title}
      subtitle={subtitle}
      actions={
        <>
          {actions}
          <div className="segmented" role="group" aria-label={`${title} view`}>
            <button type="button" aria-pressed={view === "chart"} onClick={() => setView("chart")}>
              Chart
            </button>
            <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>
              Table
            </button>
          </div>
        </>
      }
    >
      {view === "chart" ? (
        <>
          {legend && legend.length > 1 && (
            <ul className="legend" aria-label="Legend">
              {legend.map((item) => (
                <li key={item.label}>
                  <span className={`key ${item.shape === "block" ? "block" : ""}`} style={{ background: item.color }} />
                  {item.label}
                </li>
              ))}
            </ul>
          )}
          <div className="chart-area" style={grow ? { minHeight: height } : { height }}>{chart}</div>
        </>
      ) : (
        <div className="table-wrap">{table}</div>
      )}
    </Card>
  );
}

interface TooltipRow {
  label: string;
  value: string;
  color?: string;
}

export function ChartTooltip({ title, rows }: { title: string; rows: TooltipRow[] }) {
  return (
    <div className="chart-tooltip">
      <div className="title">{title}</div>
      {rows.map((row) => (
        <div className="row" key={row.label}>
          <span>
            {row.color && <span className="legend key block" style={{ background: row.color }} />}
            {row.label}
          </span>
          <span>{row.value}</span>
        </div>
      ))}
    </div>
  );
}
