import type { SourceApp } from "../api/types";
import { formatCompact } from "../lib/format";
import { duplicateRows, sourceColor } from "../lib/sources";

interface SourceFlowProps {
  sources: SourceApp[];
  /** Rows that reached the reporting tables (analytics). Optional: shown on the last node. */
  reportLabel?: string;
}

const W = 800;
const ROW = 70;
const TOP = 16;
const NODE_W = 232;
const NODE_H = 54;
const CORE_X = 392;
const OUT_X = 640;

/**
 * The data's route drawn from the ingestion log: every source application on the left, a band per
 * app whose width follows the rows it delivered, into the validated tables; rejected rows branch
 * off to quarantine. Exact numbers live in the table beside it, so the drawing can stay quiet.
 */
export function SourceFlow({ sources, reportLabel = "Reports & API" }: SourceFlowProps) {
  const n = Math.max(sources.length, 1);
  const H = TOP * 2 + n * ROW;
  const coreY = H / 2 - 40;
  const quarantineY = H / 2 + 46;
  const duplicatesY = quarantineY + 66;
  const max = Math.max(...sources.map((s) => s.rows_loaded), 1);
  const width = (rows: number) => (rows > 0 ? 3 + Math.sqrt(rows / max) * 18 : 1.5);
  const current = sources.reduce((sum, s) => sum + s.entities.reduce((a, e) => a + e.rows_current, 0), 0);
  const rejected = sources.reduce((sum, s) => sum + s.rows_rejected, 0);
  const loaded = sources.reduce((sum, s) => sum + s.rows_loaded, 0);
  const duplicates = sources.reduce((sum, s) => sum + duplicateRows(s), 0);

  const summary = sources
    .map((s) => `${s.name}: ${s.rows_loaded.toLocaleString("en-IN")} rows, ${s.rows_rejected.toLocaleString("en-IN")} rejected`)
    .join("; ");

  return (
    <>
    {/* Narrow screens get the same facts as a list; the drawing needs about 640px to read. */}
    <ul className="flow-list" aria-label="Rows delivered by each source application">
      {sources.map((s) => (
        <li key={s.id}>
          <span className="swatch" style={{ background: sourceColor(s.id) }} aria-hidden="true" />
          <span className="flow-list-name">{s.name}</span>
          <span className="num">{formatCompact(s.rows_loaded)} rows</span>
          <span className="num flow-list-rej">{s.rows_rejected ? `${formatCompact(s.rows_rejected)} quarantined` : "none quarantined"}</span>
        </li>
      ))}
      <li className="flow-list-total">
        <span className="flow-list-name">Duplicates merged</span>
        <span className="num">{formatCompact(duplicates)} rows</span>
      </li>
      <li className="flow-list-total">
        <span className="flow-list-name">Kept in validated tables</span>
        <span className="num">
          {formatCompact(current)} rows · {loaded ? ((current / loaded) * 100).toFixed(1) : "0"}%
        </span>
      </li>
    </ul>
    <svg
      className="source-flow"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`Data flow from ${sources.length} source applications into the validated tables; ${duplicates.toLocaleString("en-IN")} duplicate rows merged. ${summary}.`}
    >
      {sources.map((s, i) => {
        const y = TOP + i * ROW + NODE_H / 2;
        const x0 = NODE_W;
        const mid = (x0 + CORE_X) / 2;
        return (
          <g key={s.id}>
            <path
              d={`M${x0} ${y} C${mid} ${y}, ${mid} ${coreY}, ${CORE_X} ${coreY}`}
              stroke={sourceColor(s.id)}
              strokeWidth={width(s.rows_loaded)}
              strokeOpacity={0.5}
              fill="none"
            />
            {duplicateRows(s) > 0 && (
              <path
                d={`M${x0} ${y} C${mid} ${y}, ${mid} ${duplicatesY}, ${CORE_X} ${duplicatesY}`}
                stroke="var(--text-muted)"
                strokeWidth={1.5}
                strokeDasharray="1 4"
                strokeLinecap="round"
                strokeOpacity={0.8}
                fill="none"
              />
            )}
            {s.rows_rejected > 0 && (
              <path
                d={`M${x0} ${y} C${mid} ${y}, ${mid} ${quarantineY}, ${CORE_X} ${quarantineY}`}
                stroke="var(--status-critical)"
                strokeWidth={1.5}
                strokeDasharray="3 4"
                strokeOpacity={0.7}
                fill="none"
              />
            )}
            <rect x={0} y={y - NODE_H / 2} width={NODE_W} height={NODE_H} rx={6} className="sf-node" />
            <circle cx={20} cy={y} r={6} fill={sourceColor(s.id)} />
            <text x={36} y={y - 4} className="sf-name">
              {s.name}
            </text>
            <text x={36} y={y + 14} className="sf-meta">
              {formatCompact(s.rows_loaded)} rows · {s.entities.map((e) => e.entity.replace(/_/g, " ")).join(", ")}
            </text>
          </g>
        );
      })}

      <path
        d={`M${CORE_X + 196} ${coreY} L${OUT_X} ${coreY}`}
        stroke="var(--text-primary)"
        strokeWidth={width(current) * 0.9}
        strokeOpacity={0.85}
        fill="none"
      />

      <rect x={CORE_X} y={coreY - 32} width={196} height={64} rx={6} className="sf-core" />
      <text x={CORE_X + 16} y={coreY - 6} className="sf-name sf-on-ink">
        Validated tables
      </text>
      <text x={CORE_X + 16} y={coreY + 14} className="sf-meta sf-on-ink">
        {formatCompact(current)} rows · {loaded ? ((current / loaded) * 100).toFixed(1) : "0"}% kept
      </text>

      <rect x={CORE_X} y={quarantineY - 26} width={196} height={52} rx={6} className="sf-quarantine" />
      <text x={CORE_X + 16} y={quarantineY - 3} className="sf-name">
        Quarantine
      </text>
      <text x={CORE_X + 16} y={quarantineY + 15} className="sf-meta">
        {formatCompact(rejected)} rows rejected
      </text>

      <rect x={CORE_X} y={duplicatesY - 26} width={196} height={52} rx={6} className="sf-duplicates" />
      <text x={CORE_X + 16} y={duplicatesY - 3} className="sf-name">
        Duplicates merged
      </text>
      <text x={CORE_X + 16} y={duplicatesY + 15} className="sf-meta">
        {formatCompact(duplicates)} rows delivered twice
      </text>

      <rect x={OUT_X} y={coreY - 32} width={W - OUT_X - 1} height={64} rx={6} className="sf-out" />
      <text x={OUT_X + 16} y={coreY - 6} className="sf-name">
        {reportLabel}
      </text>
      <text x={OUT_X + 16} y={coreY + 14} className="sf-meta">
        45 named SQL queries
      </text>
    </svg>
    </>
  );
}
