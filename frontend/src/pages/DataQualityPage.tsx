import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { DataQuality } from "../api/types";
import { useApi } from "../api/useApi";
import { AsyncView } from "../components/AsyncView";
import { Card, KpiCard, ReportSection } from "../components/Card";
import { ChartCard, ChartTooltip } from "../components/ChartCard";
import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/StatusBadge";
import { axisProps, gridProps, HBAR_RADIUS } from "../lib/charts";
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
          <p>The checks every batch must pass before it reaches the reports, and the rows they kept out.</p>
        </div>
        <button type="button" className="btn" onClick={dq.reload}>Refresh</button>
      </header>

      <AsyncView state={dq} skeletonRows={6}>
        {(d) => (
          <div className="report">
            <ReportSection
              id="summary"
              title="Summary"
              description="WARN means some rows failed within the agreed threshold; FAIL means above it, which blocks the gold refresh."
              lineage={<code>ops.data_quality_results</code>}
            >
            <div className="grid kpis">
              <KpiCard label="Checks passing" value={formatInt(d.status_counts.PASS)} hint={<StatusBadge status="PASS" />} tone="good" />
              <KpiCard label="Warnings, within threshold" value={formatInt(d.status_counts.WARN)} hint={<StatusBadge status="WARN" />} tone={d.status_counts.WARN ? "warning" : undefined} />
              <KpiCard label="Failing checks" value={formatInt(d.status_counts.FAIL)} hint={<StatusBadge status="FAIL" />} tone={d.status_counts.FAIL ? "critical" : undefined} />
              <KpiCard label="Quarantined records" value={formatInt(d.rejections.reduce((n, r) => n + r.rows, 0))} hint={`Last checked ${formatDateTime(d.last_checked_at)}`} />
            </div>
            <ChartCard
              title="Checks by type"
              subtitle="Pass, warn and fail per kind of check"
              height={Math.max(types.length * 38, 120)}
              grow
              chart={
                <ul className="type-matrix">
                  {types.map((t) => {
                    const of = d.checks.filter((c) => c.check_type === t);
                    const n = (st: string) => of.filter((c) => c.status === st).length;
                    return (
                      <li key={t}>
                        <span className="type-name">{humanize(t)}</span>
                        <div className="stacked-bar" aria-hidden="true">
                          {(["PASS", "WARN", "FAIL"] as const).map((st) =>
                            n(st) ? <span key={st} data-tone={st === "PASS" ? "good" : st === "WARN" ? "warning" : "critical"} style={{ flexGrow: n(st) }} /> : null,
                          )}
                        </div>
                        <span className="type-counts">
                          {(["PASS", "WARN", "FAIL"] as const).map((st) =>
                            n(st) ? (
                              <span key={st} className="type-count-item" data-tone={st === "PASS" ? "good" : st === "WARN" ? "warning" : "critical"}>
                                <Icon name={st === "PASS" ? "check" : st === "WARN" ? "warning" : "x"} size={13} />
                                {n(st)} {st.toLowerCase()}
                              </span>
                            ) : null,
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              }
              table={
                <table>
                  <thead>
                    <tr><th scope="col">Type</th><th scope="col" className="num">Pass</th><th scope="col" className="num">Warn</th><th scope="col" className="num">Fail</th><th scope="col" className="num">Total</th></tr>
                  </thead>
                  <tbody>
                    {types.map((t) => {
                      const of = d.checks.filter((c) => c.check_type === t);
                      const n = (st: string) => of.filter((c) => c.status === st).length;
                      return (
                        <tr key={t}><td>{humanize(t)}</td><td className="num">{n("PASS")}</td><td className="num">{n("WARN")}</td><td className="num">{n("FAIL")}</td><td className="num">{of.length}</td></tr>
                      );
                    })}
                  </tbody>
                </table>
              }
            />
            </ReportSection>

            <ReportSection id="results" title="Check results" description="The latest result of every check, with the rows it flagged.">
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
            </ReportSection>

            <ReportSection
              id="quarantine"
              title="Quarantine"
              description="Rows rejected on the way in, kept for inspection instead of being dropped."
              lineage={<code>core.rejected_records</code>}
            >
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
                    <Bar dataKey="rows" fill="var(--status-critical)" maxBarSize={16} radius={HBAR_RADIUS} isAnimationActive={false} />
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
            </ReportSection>
          </div>
        )}
      </AsyncView>
    </>
  );
}
