import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { DataQuality } from "../api/types";
import { useApi } from "../api/useApi";
import { AsyncView } from "../components/AsyncView";
import { Card, KpiCard } from "../components/Card";
import { ChartCard, ChartTooltip } from "../components/ChartCard";
import { StatusBadge } from "../components/StatusBadge";
import { axisProps, gridProps, HBAR_RADIUS, seriesColor } from "../lib/charts";
import { formatDateTime, formatInt, formatPct, humanize } from "../lib/format";

interface TooltipLike {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: { label: string; rows: number } }>;
}

export function DataQualityPage() {
  const dq = useApi<DataQuality>("/data-quality");
  const [status, setStatus] = useState("all");
  const [type, setType] = useState("all");

  const types = useMemo(() => [...new Set((dq.data?.checks ?? []).map((c) => c.check_type))].sort(), [dq.data]);
  const checks = (dq.data?.checks ?? []).filter(
    (c) => (status === "all" || c.status === status) && (type === "all" || c.check_type === type),
  );

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Data quality</h1>
          <p>Validation, uniqueness, completeness, referential, freshness, volume, schema and reconciliation checks</p>
        </div>
        <button type="button" className="btn" onClick={dq.reload}>Refresh</button>
      </header>

      <AsyncView state={dq} skeletonRows={6}>
        {(d) => (
          <div className="stack">
            <div className="grid kpis">
              <KpiCard label="Checks passing" value={formatInt(d.status_counts.PASS)} hint={<StatusBadge status="PASS" />} />
              <KpiCard label="Warnings (within threshold)" value={formatInt(d.status_counts.WARN)} hint={<StatusBadge status="WARN" />} />
              <KpiCard label="Failing checks" value={formatInt(d.status_counts.FAIL)} hint={<StatusBadge status="FAIL" />} />
              <KpiCard label="Quarantined records" value={formatInt(d.rejections.reduce((n, r) => n + r.rows, 0))} hint={`Last checked ${formatDateTime(d.last_checked_at)}`} />
            </div>

            <Card
              title="Latest check results"
              subtitle="WARN = some rows failed but within the agreed threshold; FAIL = above it (blocks gold refresh on Databricks)"
              actions={
                <>
                  <label className="field">
                    Status
                    <select value={status} onChange={(e) => setStatus(e.target.value)}>
                      {["all", "FAIL", "WARN", "PASS"].map((s) => <option key={s} value={s}>{s === "all" ? "Any" : s}</option>)}
                    </select>
                  </label>
                  <label className="field">
                    Type
                    <select value={type} onChange={(e) => setType(e.target.value)}>
                      <option value="all">Any</option>
                      {types.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </label>
                </>
              }
            >
              {checks.length === 0 ? (
                <div className="state">No checks match the filters.</div>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th scope="col">Check</th><th scope="col">Entity</th><th scope="col">Type</th><th scope="col">Status</th><th scope="col" className="num">Failed rows</th><th scope="col" className="num">Rate</th><th scope="col" className="num">Threshold</th></tr>
                    </thead>
                    <tbody>
                      {checks.map((c) => (
                        <tr key={c.check_name}>
                          <td>{humanize(c.check_name)}</td>
                          <td>{c.entity}</td>
                          <td>{c.check_type}</td>
                          <td><StatusBadge status={c.status} /></td>
                          <td className="num">{formatInt(c.failed_rows)} / {formatInt(c.total_rows)}</td>
                          <td className="num">{formatPct(c.failure_rate, 2)}</td>
                          <td className="num">{formatPct(c.threshold, 1)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <ChartCard
              title="Quarantined records by reason"
              subtitle="Rows rejected by the SQL pipeline (core.rejected_records)"
              chart={
                <ResponsiveContainer>
                  <BarChart data={d.rejections.map((r) => ({ ...r, label: `${r.entity}: ${r.reason}` }))} layout="vertical"
                    margin={{ top: 0, right: 16, bottom: 0, left: 8 }}>
                    <CartesianGrid {...gridProps} vertical horizontal={false} />
                    <XAxis type="number" {...axisProps} />
                    <YAxis type="category" dataKey="label" width={250} {...axisProps} />
                    <Tooltip
                      cursor={{ fill: "var(--surface-2)" }}
                      content={(p) => {
                        const row = (p as unknown as TooltipLike).payload?.[0]?.payload;
                        if (!(p as unknown as TooltipLike).active || !row) return null;
                        return <ChartTooltip title={row.label} rows={[{ label: "Rows", value: formatInt(row.rows) }]} />;
                      }}
                    />
                    <Bar dataKey="rows" fill={seriesColor(1)} maxBarSize={16} radius={HBAR_RADIUS} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              }
              table={
                <table>
                  <thead><tr><th scope="col">Entity</th><th scope="col">Reason</th><th scope="col" className="num">Rows</th></tr></thead>
                  <tbody>
                    {d.rejections.map((r) => (
                      <tr key={`${r.entity}-${r.reason}`}><td>{r.entity}</td><td>{r.reason}</td><td className="num">{formatInt(r.rows)}</td></tr>
                    ))}
                  </tbody>
                </table>
              }
            />
          </div>
        )}
      </AsyncView>
    </>
  );
}
