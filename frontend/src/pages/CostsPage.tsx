import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { query } from "../api/client";
import type { CostSummary, CostTrends } from "../api/types";
import { useApi } from "../api/useApi";
import { AsyncView } from "../components/AsyncView";
import { Card, KpiCard, ReportSection } from "../components/Card";
import { Icon } from "../components/Icon";
import { ChartCard, ChartTooltip } from "../components/ChartCard";
import { SourceBadge, StatusBadge } from "../components/StatusBadge";
import { axisProps, gridProps, HBAR_RADIUS, seriesColor } from "../lib/charts";
import { formatDate, formatPct, formatUsd } from "../lib/format";

interface TooltipLike {
  active?: boolean;
  label?: string | number;
  payload?: ReadonlyArray<{ value?: unknown; payload?: Record<string, unknown> }>;
}

export function CostsPage() {
  const [month, setMonth] = useState<string | null>(null);
  const summary = useApi<CostSummary>(`/costs/summary${query({ month })}`);
  const trends = useApi<CostTrends>("/costs/trends");
  const months = [...new Set((trends.data?.daily_totals ?? []).map((p) => p.date.slice(0, 7)))].sort().reverse();

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Cloud costs</h1>
          <p>What running the platform costs, which workloads spend it, and whether the month stays inside its budget.</p>
        </div>
        <label className="field">
          Month
          <select value={month ?? summary.data?.month ?? ""} onChange={(e) => setMonth(e.target.value)}>
            {months.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
      </header>

      <div className="notice" style={{ marginBottom: 24 }}>
        <Icon name="warning" size={16} />
        <span>
        <SourceBadge sources={summary.data?.sources ?? []} /> These figures are <strong>simulated</strong> from pipeline
        run times with illustrative unit prices. Real AWS (CUR) or Databricks billing rows would be labelled with their
        source. See <code>docs/cost-management.md</code>.
        </span>
      </div>

      <div className="report">
        <AsyncView state={summary} skeletonRows={4}>
          {(s) => {
            const used = s.total_usd / s.budget_usd;
            return (
              <>
                <ReportSection id="budget" title="Budget" description={`Month to date for ${s.month}, against the monthly budget.`} lineage={<code>ops.cloud_costs</code>}>
                <div className="grid kpis">
                  <KpiCard label={`Spend ${s.month}`} value={formatUsd(s.total_usd)} hint={`as of ${formatDate(s.as_of)}`}
                    tone={s.budget_status === "OVER_BUDGET" ? "critical" : undefined} />
                  <KpiCard label="Forecast (month end)" value={formatUsd(s.forecast_usd)} hint="linear run-rate" />
                  <KpiCard label="Monthly budget" value={formatUsd(s.budget_usd, 0)} hint={<StatusBadge status={s.budget_status} />} />
                  <KpiCard label="Untagged spend" value={formatUsd(s.untagged_usd)} hint="cannot be charged back" />
                </div>
                <Card title="Budget used" subtitle={`${formatPct(used)} of ${formatUsd(s.budget_usd, 0)}`}>
                  <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(used * 100)}
                    aria-label="Budget used">
                    <span style={{ width: `${Math.min(used, 1) * 100}%`,
                      background: used > 1 ? "var(--status-critical)" : used > 0.8 ? "var(--status-warning)" : "var(--status-good)" }} />
                  </div>
                </Card>
                </ReportSection>
                <ReportSection id="breakdown" title="Where the money goes" description="Spend split by cloud service and by the workload that used it.">
                <div className="grid two">
                  {([["Spend by service", s.by_service], ["Spend by workload", s.by_workload]] as const).map(([title, rows]) => (
                    <ChartCard
                      key={title}
                      title={title}
                      subtitle={s.month}
                      chart={
                        <ResponsiveContainer>
                          <BarChart data={rows.slice(0, 10)} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 8 }}>
                            <CartesianGrid {...gridProps} vertical horizontal={false} />
                            <XAxis type="number" tickFormatter={(v: number) => formatUsd(v, 0)} {...axisProps} />
                            <YAxis type="category" dataKey="name" width={170} {...axisProps} />
                            <Tooltip
                              cursor={{ fill: "var(--surface-2)" }}
                              content={(p) => {
                                const row = (p as unknown as TooltipLike).payload?.[0]?.payload;
                                if (!(p as unknown as TooltipLike).active || !row) return null;
                                return <ChartTooltip title={String(row.name)} rows={[
                                  { label: "Cost", value: formatUsd(Number(row.cost_usd)) },
                                  { label: "Share", value: formatPct(Number(row.share)) },
                                ]} />;
                              }}
                            />
                            <Bar dataKey="cost_usd" fill={seriesColor(1)} maxBarSize={18} radius={HBAR_RADIUS} isAnimationActive={false} />
                          </BarChart>
                        </ResponsiveContainer>
                      }
                      table={
                        <table>
                          <thead><tr><th scope="col">Name</th><th scope="col" className="num">Cost</th><th scope="col" className="num">Share</th></tr></thead>
                          <tbody>{rows.map((r) => <tr key={r.name}><td>{r.name}</td><td className="num">{formatUsd(r.cost_usd)}</td><td className="num">{formatPct(r.share)}</td></tr>)}</tbody>
                        </table>
                      }
                    />
                  ))}
                </div>
                <Card title="Most expensive jobs" subtitle="Compute cost per pipeline in the month">
                  <div className="table-wrap">
                    <table>
                      <thead><tr><th scope="col">Pipeline</th><th scope="col" className="num">Cost</th><th scope="col" className="num">Runs</th><th scope="col" className="num">Cost per run</th></tr></thead>
                      <tbody>
                        {s.expensive_jobs.map((j) => (
                          <tr key={j.pipeline_name}><td>{j.pipeline_name}</td><td className="num">{formatUsd(j.cost_usd)}</td><td className="num">{j.runs}</td><td className="num">{formatUsd(j.cost_per_run_usd, 3)}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
                </ReportSection>
              </>
            );
          }}
        </AsyncView>

        <ReportSection id="trend" title="Trend" description="Daily spend, with days flagged as anomalies by the cost alert rule.">
        <AsyncView state={trends} isEmpty={(t) => t.daily_totals.length === 0}>
          {(t) => (
            <ChartCard
              title="Daily spend and anomalies"
              subtitle={`${t.anomalies.length} anomalous day(s): more than 3 standard deviations above the trailing 14-day mean`}
              legend={[
                { label: "Daily spend", color: seriesColor(1) },
                { label: "Anomaly (red point)", color: "var(--status-critical)", shape: "block" },
              ]}
              chart={
                <ResponsiveContainer>
                  <LineChart data={t.daily_totals} margin={{ top: 16, right: 12, bottom: 0, left: 0 }}>
                    <CartesianGrid {...gridProps} />
                    <XAxis dataKey="date" tickFormatter={formatDate} minTickGap={24} {...axisProps} />
                    <YAxis tickFormatter={(v: number) => formatUsd(v, 0)} width={56} {...axisProps} />
                    <Tooltip
                      cursor={{ stroke: "var(--axis)" }}
                      content={(p) => {
                        const tt = p as unknown as TooltipLike;
                        if (!tt.active || !tt.payload?.length) return null;
                        const date = String(tt.label);
                        const anomaly = t.anomalies.find((a) => a.date === date);
                        return <ChartTooltip title={formatDate(date)} rows={[
                          { label: "Spend", value: formatUsd(Number(tt.payload[0]?.value)) },
                          ...(anomaly ? [{ label: "Anomaly (z-score)", value: anomaly.z_score.toFixed(1) }] : []),
                        ]} />;
                      }}
                    />
                    <Line dataKey="cost_usd" stroke={seriesColor(1)} strokeWidth={2} dot={false} isAnimationActive={false}
                      activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }} />
                    {t.anomalies.map((a) => (
                      // No text labels: adjacent anomaly days would collide. The legend, tooltip and
                      // table view name them instead.
                      <ReferenceDot key={a.date} x={a.date} y={a.cost_usd} r={5} fill="var(--status-critical)"
                        stroke="var(--surface-1)" strokeWidth={2} />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              }
              table={
                <table>
                  <thead><tr><th scope="col">Date</th><th scope="col" className="num">Spend</th><th scope="col">Anomaly</th></tr></thead>
                  <tbody>
                    {t.daily_totals.map((p) => {
                      const anomaly = t.anomalies.find((a) => a.date === p.date);
                      return <tr key={p.date}><td>{formatDate(p.date)}</td><td className="num">{formatUsd(p.cost_usd)}</td><td>{anomaly ? `z = ${anomaly.z_score}` : ""}</td></tr>;
                    })}
                  </tbody>
                </table>
              }
            />
          )}
        </AsyncView>
        </ReportSection>
      </div>
    </>
  );
}

