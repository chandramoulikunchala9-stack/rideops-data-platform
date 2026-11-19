import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { query } from "../api/client";
import type { Bookings, Cancellations, Driver, Page, Payments, Revenue } from "../api/types";
import { useApi } from "../api/useApi";
import { AsyncView } from "../components/AsyncView";
import { Card } from "../components/Card";
import { ChartCard, ChartTooltip } from "../components/ChartCard";
import { DateRangeFilter, Pagination } from "../components/Filters";
import {
  axisProps,
  BAR_RADIUS,
  BAR_SIZE,
  CITY_NAMES,
  cityColor,
  gridProps,
  HBAR_RADIUS,
  seriesColor,
} from "../lib/charts";
import { useFilters } from "../lib/filters";
import { formatDate, formatInr, formatInrCompact, formatInt, formatPct, humanize } from "../lib/format";

interface TooltipLike {
  active?: boolean;
  label?: string | number;
  payload?: ReadonlyArray<{ dataKey?: unknown; value?: unknown; payload?: Record<string, unknown> }>;
}

const STATUS_SERIES = [
  { key: "completed", label: "Completed", color: seriesColor(1) },
  { key: "cancelled", label: "Cancelled", color: seriesColor(2) },
  { key: "no_driver_found", label: "No driver found", color: seriesColor(3) },
] as const;

const PAGE_SIZE = 10;

export function AnalyticsPage() {
  const { startDate, endDate, city } = useFilters();
  const filter = query({ start_date: startDate, end_date: endDate, city });
  const revenue = useApi<Revenue>(`/analytics/revenue${query({ start_date: startDate, end_date: endDate })}`);
  const bookings = useApi<Bookings>(`/analytics/bookings${filter}`);
  const cancellations = useApi<Cancellations>(`/analytics/cancellations${filter}`);
  const payments = useApi<Payments>(`/analytics/payments${query({ start_date: startDate, end_date: endDate })}`);

  const [sort, setSort] = useState("revenue");
  const [offset, setOffset] = useState(0);
  const drivers = useApi<Page<Driver>>(`/analytics/drivers${query({ city, sort, limit: PAGE_SIZE, offset })}`);

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Business analytics</h1>
          <p>Revenue, demand, drivers, cancellations and payments{city && ` · ${CITY_NAMES[city]}`}</p>
        </div>
        <DateRangeFilter dataEnd={revenue.data?.period.end ?? null} showCity />
      </header>

      <div className="stack">
        <div className="grid two">
          <AsyncView state={revenue} isEmpty={(r) => r.by_city.length === 0}>
            {(r) => (
              <ChartCard
                title="Revenue by city"
                subtitle="Selected period, INR (all cities)"
                chart={
                  <ResponsiveContainer>
                    <BarChart data={r.by_city} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                      <CartesianGrid {...gridProps} />
                      <XAxis dataKey="city_name" {...axisProps} />
                      <YAxis tickFormatter={formatInrCompact} width={56} {...axisProps} />
                      <Tooltip
                        cursor={{ fill: "var(--surface-2)" }}
                        content={(p) => {
                          const t = p as unknown as TooltipLike;
                          const row = t.payload?.[0]?.payload;
                          if (!t.active || !row) return null;
                          return (
                            <ChartTooltip
                              title={String(row.city_name)}
                              rows={[
                                { label: "Revenue", value: formatInr(Number(row.revenue_inr)) },
                                { label: "Bookings", value: formatInt(Number(row.bookings)) },
                              ]}
                            />
                          );
                        }}
                      />
                      <Bar dataKey="revenue_inr" maxBarSize={BAR_SIZE} radius={BAR_RADIUS} isAnimationActive={false}>
                        {r.by_city.map((c) => (
                          <Cell key={c.city} fill={cityColor(c.city)} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                }
                table={
                  <table>
                    <thead>
                      <tr><th scope="col">City</th><th scope="col" className="num">Revenue</th><th scope="col" className="num">Bookings</th></tr>
                    </thead>
                    <tbody>
                      {r.by_city.map((c) => (
                        <tr key={c.city}><td>{c.city_name}</td><td className="num">{formatInr(c.revenue_inr)}</td><td className="num">{formatInt(c.bookings)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                }
              />
            )}
          </AsyncView>

          <AsyncView state={bookings} isEmpty={(b) => b.daily.length === 0}>
            {(b) => (
              <ChartCard
                title="Daily bookings by outcome"
                subtitle={city ? CITY_NAMES[city] : "All cities"}
                legend={STATUS_SERIES.map((s) => ({ label: s.label, color: s.color, shape: "block" }))}
                chart={
                  <ResponsiveContainer>
                    <BarChart data={b.daily} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                      <CartesianGrid {...gridProps} />
                      <XAxis dataKey="date" tickFormatter={formatDate} minTickGap={24} {...axisProps} />
                      <YAxis width={48} {...axisProps} />
                      <Tooltip
                        cursor={{ fill: "var(--surface-2)" }}
                        content={(p) => {
                          const t = p as unknown as TooltipLike;
                          const row = t.payload?.[0]?.payload;
                          if (!t.active || !row) return null;
                          return (
                            <ChartTooltip
                              title={formatDate(String(t.label))}
                              rows={[
                                ...STATUS_SERIES.map((s) => ({ label: s.label, value: formatInt(Number(row[s.key])), color: s.color })),
                                { label: "Cancellation rate", value: formatPct(Number(row.cancellation_rate)) },
                              ]}
                            />
                          );
                        }}
                      />
                      {STATUS_SERIES.map((s, i) => (
                        <Bar key={s.key} dataKey={s.key} stackId="status" fill={s.color} maxBarSize={BAR_SIZE}
                          stroke="var(--surface-1)" strokeWidth={2} isAnimationActive={false}
                          radius={i === STATUS_SERIES.length - 1 ? BAR_RADIUS : 0} />
                      ))}
                    </BarChart>
                  </ResponsiveContainer>
                }
                table={
                  <table>
                    <thead>
                      <tr><th scope="col">Date</th>{STATUS_SERIES.map((s) => <th key={s.key} scope="col" className="num">{s.label}</th>)}<th scope="col" className="num">Cancel rate</th></tr>
                    </thead>
                    <tbody>
                      {b.daily.map((d) => (
                        <tr key={d.date}>
                          <td>{formatDate(d.date)}</td>
                          <td className="num">{formatInt(d.completed)}</td>
                          <td className="num">{formatInt(d.cancelled)}</td>
                          <td className="num">{formatInt(d.no_driver_found)}</td>
                          <td className="num">{formatPct(d.cancellation_rate)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                }
              />
            )}
          </AsyncView>
        </div>

        <div className="grid two">
          <AsyncView state={cancellations} isEmpty={(c) => c.by_reason.length === 0}>
            {(c) => {
              const rows = c.by_reason.map((r) => ({ ...r, label: `${humanize(r.cancellation_reason)} (${r.cancelled_by})` }));
              return (
                <ChartCard
                  title="Cancellation reasons"
                  subtitle="Who cancelled, and why"
                  chart={
                    <ResponsiveContainer>
                      <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 8 }}>
                        <CartesianGrid {...gridProps} vertical horizontal={false} />
                        <XAxis type="number" {...axisProps} />
                        <YAxis type="category" dataKey="label" width={210} {...axisProps} />
                        <Tooltip
                          cursor={{ fill: "var(--surface-2)" }}
                          content={(p) => {
                            const t = p as unknown as TooltipLike;
                            const row = t.payload?.[0]?.payload;
                            if (!t.active || !row) return null;
                            return (
                              <ChartTooltip title={String(row.label)} rows={[
                                { label: "Cancellations", value: formatInt(Number(row.cancellations)) },
                                { label: "Fees collected", value: formatInr(Number(row.fees_collected)) },
                              ]} />
                            );
                          }}
                        />
                        <Bar dataKey="cancellations" fill={seriesColor(2)} maxBarSize={18} radius={HBAR_RADIUS} isAnimationActive={false} />
                      </BarChart>
                    </ResponsiveContainer>
                  }
                  table={
                    <table>
                      <thead><tr><th scope="col">Reason</th><th scope="col">By</th><th scope="col" className="num">Cancellations</th><th scope="col" className="num">Fees</th></tr></thead>
                      <tbody>
                        {c.by_reason.map((r) => (
                          <tr key={`${r.cancelled_by}-${r.cancellation_reason}`}>
                            <td>{humanize(r.cancellation_reason)}</td><td>{r.cancelled_by}</td>
                            <td className="num">{formatInt(r.cancellations)}</td><td className="num">{formatInr(r.fees_collected)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  }
                />
              );
            }}
          </AsyncView>

          <Card title="Payments by method" subtitle="Charge attempts in the selected period">
            <AsyncView state={payments} isEmpty={(p) => p.by_method.length === 0}>
              {(p) => (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th scope="col">Method</th><th scope="col" className="num">Attempts</th><th scope="col" className="num">Failed</th><th scope="col">Failure rate</th><th scope="col" className="num">Charged</th><th scope="col" className="num">Refunded</th></tr>
                    </thead>
                    <tbody>
                      {p.by_method.map((m) => (
                        <tr key={m.method}>
                          <td>{m.method.toUpperCase()}</td>
                          <td className="num">{formatInt(m.attempts)}</td>
                          <td className="num">{formatInt(m.failed_attempts)}</td>
                          <td style={{ minWidth: 120 }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              <div className="meter" style={{ flex: 1 }} aria-hidden="true">
                                <span style={{ width: `${Math.min(m.failure_rate * 100 * 5, 100)}%`, background: seriesColor(2) }} />
                              </div>
                              <span className="mono">{formatPct(m.failure_rate)}</span>
                            </div>
                          </td>
                          <td className="num">{formatInrCompact(m.charged_amount)}</td>
                          <td className="num">{formatInrCompact(m.refunded_amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "8px 0 0" }}>Bar scale: full width = 20% failure rate.</p>
                </div>
              )}
            </AsyncView>
          </Card>
        </div>

        <Card
          title="Driver performance"
          subtitle={`All-time leaderboard${city ? ` · ${CITY_NAMES[city]}` : ""}`}
          actions={
            <label className="field">
              Sort by
              <select value={sort} onChange={(e) => { setSort(e.target.value); setOffset(0); }}>
                <option value="revenue">Revenue</option>
                <option value="rides">Completed rides</option>
                <option value="completion_rate">Completion rate</option>
                <option value="rating">Rating</option>
              </select>
            </label>
          }
        >
          <AsyncView state={drivers} isEmpty={(d) => d.items.length === 0}>
            {(d) => (
              <>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Driver</th><th scope="col">City</th><th scope="col" className="num">Rating</th>
                        <th scope="col" className="num">Completed</th><th scope="col" className="num">Completion</th>
                        <th scope="col" className="num">Driver cancels</th><th scope="col" className="num">Revenue</th>
                        <th scope="col" className="num">City rank</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.items.map((r) => (
                        <tr key={r.driver_id}>
                          <td>{r.full_name ?? "–"} <span className="mono" style={{ color: "var(--text-muted)" }}>{r.driver_id}</span></td>
                          <td>{r.city ? CITY_NAMES[r.city] ?? r.city : "–"}</td>
                          <td className="num">{r.rating?.toFixed(2) ?? "–"}</td>
                          <td className="num">{formatInt(r.completed_rides)}</td>
                          <td className="num">{formatPct(r.completion_rate)}</td>
                          <td className="num">{formatInt(r.driver_cancellations)}</td>
                          <td className="num">{formatInr(r.total_revenue)}</td>
                          <td className="num">#{r.revenue_rank_in_city}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pagination total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} />
              </>
            )}
          </AsyncView>
        </Card>
      </div>
    </>
  );
}
