import { Link } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { query } from "../api/client";
import type { Alert, Overview, Revenue } from "../api/types";
import { useApi } from "../api/useApi";
import { AsyncView } from "../components/AsyncView";
import { Card, KpiCard } from "../components/Card";
import { ChartCard, ChartTooltip } from "../components/ChartCard";
import { DateRangeFilter } from "../components/Filters";
import { SourceBadge, StatusBadge } from "../components/StatusBadge";
import { axisProps, CITY_NAMES, CITY_ORDER, cityColor, gridProps, pivotByCity } from "../lib/charts";
import { useFilters } from "../lib/filters";
import { formatDate, formatDateTime, formatInr, formatInrCompact, formatInt, formatPct, formatUsd } from "../lib/format";

interface TooltipLike {
  active?: boolean;
  label?: string | number;
  payload?: ReadonlyArray<{ dataKey?: unknown; value?: unknown }>;
}

export function OverviewPage() {
  const { startDate, endDate } = useFilters();
  const range = query({ start_date: startDate, end_date: endDate });
  const overview = useApi<Overview>(`/analytics/overview${range}`);
  const revenue = useApi<Revenue>(`/analytics/revenue${range}`);
  const alerts = useApi<Alert[]>("/monitoring/alerts");

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Overview</h1>
          <p>
            Business and platform health
            {overview.data && ` · ${formatDate(overview.data.period.start)} – ${formatDate(overview.data.period.end)}`}
          </p>
        </div>
        <DateRangeFilter dataEnd={overview.data?.data_as_of ?? null} />
      </header>

      <div className="stack">
        <AsyncView state={overview} skeletonRows={4}>
          {(o) => (
            <div className="grid kpis">
              <KpiCard label="Total bookings" value={formatInt(o.total_bookings)} />
              <KpiCard label="Total revenue" value={formatInrCompact(o.total_revenue_inr)} hint={formatInr(o.total_revenue_inr)} />
              <KpiCard label="Completed rides" value={formatInt(o.completed_rides)} />
              <KpiCard label="Active drivers" value={formatInt(o.active_drivers)} hint={`${formatInt(o.active_customers)} active customers`} />
              <KpiCard label="Cancellation rate" value={formatPct(o.cancellation_rate)} />
              <KpiCard label="Pipeline success rate" value={formatPct(o.pipeline_success_rate)} hint="Runs in the last 30 days" />
              <KpiCard
                label="Failed runs (30 days)"
                value={formatInt(o.failed_runs_30d)}
                hint={<Link to="/pipelines">View pipelines</Link>}
              />
              <KpiCard
                label={`Cloud spend${o.cloud_spend.month ? ` (${o.cloud_spend.month})` : ""}`}
                value={formatUsd(o.cloud_spend.amount_usd, 0)}
                hint={<SourceBadge sources={o.cloud_spend.sources} />}
              />
            </div>
          )}
        </AsyncView>

        <AsyncView state={revenue} isEmpty={(r) => r.series.length === 0}>
          {(r) => {
            const rows = pivotByCity(r.series);
            const cities = CITY_ORDER.filter((c) => r.by_city.some((b) => b.city === c));
            return (
              <ChartCard
                title="Daily revenue by city"
                subtitle="Completed rides plus cancellation fees, INR"
                legend={cities.map((c) => ({ label: CITY_NAMES[c] ?? c, color: cityColor(c) }))}
                chart={
                  <ResponsiveContainer>
                    <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                      <CartesianGrid {...gridProps} />
                      <XAxis dataKey="period" tickFormatter={formatDate} minTickGap={24} {...axisProps} />
                      <YAxis tickFormatter={formatInrCompact} width={56} {...axisProps} />
                      <Tooltip
                        cursor={{ stroke: "var(--axis)" }}
                        content={(p) => {
                          const t = p as unknown as TooltipLike;
                          if (!t.active || !t.payload?.length) return null;
                          return (
                            <ChartTooltip
                              title={formatDate(String(t.label))}
                              rows={t.payload.map((item) => ({
                                label: CITY_NAMES[String(item.dataKey)] ?? String(item.dataKey),
                                value: formatInr(Number(item.value)),
                                color: cityColor(String(item.dataKey)),
                              }))}
                            />
                          );
                        }}
                      />
                      {cities.map((c) => (
                        <Line key={c} type="monotone" dataKey={c} name={CITY_NAMES[c]} stroke={cityColor(c)}
                          strokeWidth={2} dot={false} isAnimationActive={false}
                          activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }} />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                }
                table={
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">City</th>
                        <th scope="col" className="num">Bookings</th>
                        <th scope="col" className="num">Revenue</th>
                        <th scope="col" className="num">Avg per booking</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.by_city.map((c) => (
                        <tr key={c.city}>
                          <td>{c.city_name}</td>
                          <td className="num">{formatInt(c.bookings)}</td>
                          <td className="num">{formatInr(c.revenue_inr)}</td>
                          <td className="num">{formatInr(c.avg_fare_inr, 2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                }
              />
            );
          }}
        </AsyncView>

        <div className="grid two">
          <Card title="Recent pipeline failures">
            <AsyncView state={overview} isEmpty={(o) => o.recent_failures.length === 0} empty="No failed runs. 🎉">
              {(o) => (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Pipeline</th>
                        <th scope="col">Started</th>
                        <th scope="col">Error</th>
                      </tr>
                    </thead>
                    <tbody>
                      {o.recent_failures.map((f) => (
                        <tr key={f.run_id}>
                          <td>
                            <Link to={`/pipelines/${f.pipeline_name}?run=${f.run_id}`}>{f.pipeline_name}</Link>
                          </td>
                          <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(f.start_ts)}</td>
                          <td style={{ color: "var(--text-secondary)" }}>{(f.error_message ?? "").slice(0, 90)}…</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </AsyncView>
          </Card>

          <Card title="Active alerts" actions={<Link to="/monitoring">All alerts</Link>}>
            <AsyncView state={alerts} isEmpty={(a) => a.length === 0} empty="No active alerts.">
              {(list) => (
                <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 10 }}>
                  {list.slice(0, 5).map((a) => (
                    <li key={a.alert_id} style={{ display: "grid", gap: 2 }}>
                      <div className="toolbar">
                        <StatusBadge status={a.severity} />
                        <strong>{a.rule.replace(/_/g, " ")}</strong>
                        <span style={{ color: "var(--text-muted)" }}>{a.subject}</span>
                      </div>
                      <span style={{ color: "var(--text-secondary)", fontSize: 14 }}>{a.message}</span>
                    </li>
                  ))}
                </ul>
              )}
            </AsyncView>
          </Card>
        </div>
      </div>
    </>
  );
}
