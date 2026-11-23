import { Link } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { query } from "../api/client";
import type { Alert, AppFunnel, DataQuality, MonitoringSummary, Overview, Revenue, Sources } from "../api/types";
import { useApi } from "../api/useApi";
import { AsyncView } from "../components/AsyncView";
import { Card, KpiCard, ReportSection } from "../components/Card";
import { ChartCard, ChartTooltip } from "../components/ChartCard";
import { CityMap } from "../components/CityMap";
import { DateRangeFilter } from "../components/Filters";
import { Icon } from "../components/Icon";
import { SourceFlow } from "../components/SourceFlow";
import { SourceBadge, StatusBadge } from "../components/StatusBadge";
import { axisProps, CITY_NAMES, CITY_ORDER, cityColor, gridProps, pivotByCity } from "../lib/charts";
import { useFilters } from "../lib/filters";
import {
  formatDate,
  formatDateTime,
  formatInr,
  formatInrCompact,
  formatInt,
  formatPct,
  formatUsd,
  humanize,
  relativeTime,
} from "../lib/format";
import { duplicateRows, sourceList } from "../lib/sources";

interface TooltipLike {
  active?: boolean;
  label?: string | number;
  payload?: ReadonlyArray<{ dataKey?: unknown; value?: unknown }>;
}

const count = (record: Record<string, number> | undefined, key: string) => record?.[key] ?? 0;

export function OverviewPage() {
  const { startDate, endDate } = useFilters();
  const range = query({ start_date: startDate, end_date: endDate });
  const overview = useApi<Overview>(`/analytics/overview${range}`);
  const revenue = useApi<Revenue>(`/analytics/revenue${range}`);
  const apps = useApi<AppFunnel>(`/analytics/apps${range}`);
  const sources = useApi<Sources>("/sources");
  const quality = useApi<DataQuality>("/data-quality");
  const monitoring = useApi<MonitoringSummary>("/monitoring/summary");
  const alerts = useApi<Alert[]>("/monitoring/alerts");

  const period = overview.data ? `${formatDate(overview.data.period.start)} – ${formatDate(overview.data.period.end)}` : null;

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Overview</h1>
          <p>
            Where the data came from, whether it passed its checks, and what it says about the business
            {period && ` · ${period}`}
          </p>
        </div>
        <DateRangeFilter dataEnd={overview.data?.data_as_of ?? null} />
      </header>

      <div className="report">
        {/* ------------------------------------------------------------ Sources */}
        <ReportSection
          id="sources"
          title="Sources"
          description="The applications that delivered data, and how many rows survived validation."
          lineage={<code>ops.ingestion_log</code>}
          actions={
            <Link className="section-link" to="/sources">
              Source apps <Icon name="arrowRight" size={14} />
            </Link>
          }
        >
          <AsyncView state={sources} skeletonRows={5} isEmpty={(s) => sourceList(s).length === 0} empty="No source has delivered data yet.">
            {(body) => {
              const list = sourceList(body);
              const received = list.reduce((a, s) => a + s.rows_loaded, 0);
              const quarantined = list.reduce((a, s) => a + s.rows_rejected, 0);
              // Rows delivered more than once (re-sent files, duplicate deliveries) are merged away,
              // so received = kept + quarantined + duplicates removed.
              const duplicates = list.reduce((a, s) => a + duplicateRows(s), 0);
              const lastFile = list.map((s) => s.last_loaded_at).filter(Boolean).sort().at(-1) ?? null;
              const failing = list.filter((s) => s.status === "fail").length;
              return (
                <div className="band-grid">
                  <Card className="flow-card">
                    <div className="flow-wrap">
                      <SourceFlow sources={list} />
                    </div>
                  </Card>
                  <Card title="Delivery">
                    <dl className="facts two-col">
                      <div>
                        <dt>Last file received</dt>
                        <dd>{relativeTime(lastFile)}</dd>
                        <dd className="fact-note">{formatDateTime(lastFile)}</dd>
                      </div>
                      <div>
                        <dt>Rows received</dt>
                        <dd>{formatInt(received)}</dd>
                        <dd className="fact-note">from {list.length} applications</dd>
                      </div>
                      <div>
                        <dt>Quarantined</dt>
                        <dd>{formatInt(quarantined)}</dd>
                        <dd className="fact-note">{received ? formatPct(quarantined / received, 2) : "–"} of received rows</dd>
                      </div>
                      <div>
                        <dt>Removed as duplicates</dt>
                        <dd>{formatInt(duplicates)}</dd>
                        <dd className="fact-note">rows delivered more than once, merged into one</dd>
                      </div>
                      <div>
                        <dt>Files that failed to load</dt>
                        <dd>{failing ? `${failing} application${failing > 1 ? "s" : ""}` : "None"}</dd>
                      </div>
                    </dl>
                  </Card>
                </div>
              );
            }}
          </AsyncView>
        </ReportSection>

        {/* ------------------------------------------------------------ Checks */}
        <ReportSection
          id="checks"
          title="Checks"
          description="Data-quality checks and pipeline runs that stand between the sources and the reports."
          lineage={
            <>
              <code>ops.data_quality_results</code> · <code>ops.pipeline_runs</code>
            </>
          }
          actions={
            <Link className="section-link" to="/data-quality">
              Data quality <Icon name="arrowRight" size={14} />
            </Link>
          }
        >
          <div className="grid three">
            <Card title="Data-quality checks" subtitle={quality.data?.last_checked_at ? `Last run ${relativeTime(quality.data.last_checked_at)}` : undefined}>
              <AsyncView state={quality} skeletonRows={3}>
                {(q) => {
                  const pass = count(q.status_counts, "PASS");
                  const warn = count(q.status_counts, "WARN");
                  const fail = count(q.status_counts, "FAIL");
                  const total = pass + warn + fail;
                  return (
                    <div className="check-summary">
                      <p className="big-figure">
                        {formatInt(pass)}
                        <span> of {formatInt(total)} pass</span>
                      </p>
                      <div className="stacked-bar" role="img" aria-label={`${pass} pass, ${warn} warn, ${fail} fail`}>
                        {[
                          ["good", pass],
                          ["warning", warn],
                          ["critical", fail],
                        ].map(([tone, n]) =>
                          Number(n) > 0 ? (
                            <span key={tone} data-tone={tone} style={{ flexGrow: Number(n) }} />
                          ) : null,
                        )}
                      </div>
                      <div className="toolbar">
                        <StatusBadge status="PASS" /> {pass}
                        <StatusBadge status="WARN" /> {warn}
                        <StatusBadge status="FAIL" /> {fail}
                      </div>
                    </div>
                  );
                }}
              </AsyncView>
            </Card>

            <Card title="Why rows were quarantined" subtitle="Top reasons across all sources">
              <AsyncView state={quality} skeletonRows={4} isEmpty={(q) => !q.rejections?.length} empty="No rows were quarantined.">
                {(q) => {
                  const top = [...q.rejections].sort((a, b) => b.rows - a.rows).slice(0, 5);
                  const max = Math.max(...top.map((r) => r.rows), 1);
                  return (
                    <ul className="bar-list">
                      {top.map((r) => (
                        <li key={`${r.entity}-${r.reason}`}>
                          <div className="bar-list-label">
                            <span>
                              {r.reason}
                              <span className="bar-list-sub"> · {humanize(r.entity)}</span>
                            </span>
                            <strong>{formatInt(r.rows)}</strong>
                          </div>
                          <div className="meter">
                            <span style={{ width: `${(r.rows / max) * 100}%`, background: "var(--status-critical)" }} />
                          </div>
                        </li>
                      ))}
                    </ul>
                  );
                }}
              </AsyncView>
            </Card>

            <Card title="Pipelines and alerts" subtitle="Runs started in the last 30 days, alerts open now">
              <AsyncView state={overview} skeletonRows={4}>
                {(o) => {
                  const critical = count(monitoring.data?.active_alerts, "CRITICAL");
                  const warning = count(monitoring.data?.active_alerts, "WARNING");
                  return (
                    <dl className="facts compact">
                      <div>
                        <dt>Run success rate</dt>
                        <dd>{formatPct(o.pipeline_success_rate)}</dd>
                      </div>
                      <div>
                        <dt>Failed runs</dt>
                        <dd>
                          <Link to="/pipelines">{formatInt(o.failed_runs_30d)}</Link>
                        </dd>
                      </div>
                      <div>
                        <dt>Active alerts</dt>
                        <dd>
                          <Link to="/monitoring">{formatInt(critical + warning)}</Link>
                          <span className="fact-note"> {critical} critical · {warning} warning</span>
                        </dd>
                      </div>
                      <div>
                        <dt>Open incidents</dt>
                        <dd>{formatInt(monitoring.data?.open_incidents ?? 0)}</dd>
                      </div>
                    </dl>
                  );
                }}
              </AsyncView>
            </Card>
          </div>

          <div className="grid two">
            <Card title="Recent pipeline failures" subtitle="The five latest failed runs, any date" actions={<Link to="/pipelines">All pipelines</Link>}>
              <AsyncView state={overview} isEmpty={(o) => o.recent_failures.length === 0} empty="No pipeline run has failed yet.">
                {(o) => (
                  <div className="table-wrap">
                    <table className="failures-table">
                      <thead>
                        <tr>
                          <th scope="col">Pipeline</th>
                          <th scope="col">Started</th>
                          <th scope="col" className="col-error">Error</th>
                        </tr>
                      </thead>
                      <tbody>
                        {o.recent_failures.map((f) => (
                          <tr key={f.run_id}>
                            <td>
                              <Link to={`/pipelines/${f.pipeline_name}?run=${f.run_id}`}>{f.pipeline_name}</Link>
                            </td>
                            <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(f.start_ts)}</td>
                            <td className="col-error" title={f.error_message ?? ""}>
                              <div className="clip-2">{f.error_message ?? "–"}</div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </AsyncView>
            </Card>

            <Card title="Active alerts" actions={<Link to="/monitoring">All alerts</Link>}>
              <AsyncView state={alerts} isEmpty={(a) => !Array.isArray(a) || a.length === 0} empty="No active alerts. Rules are evaluated after every pipeline run.">
                {(list) => (
                  <ul className="alert-list">
                    {list.slice(0, 5).map((a) => (
                      <li key={a.alert_id}>
                        <div className="alert-head">
                          <StatusBadge status={a.severity} />
                          <strong>{humanize(a.rule)}</strong>
                          <span className="mono">{a.subject}</span>
                        </div>
                        <p>{a.message}</p>
                      </li>
                    ))}
                  </ul>
                )}
              </AsyncView>
            </Card>
          </div>
        </ReportSection>

        {/* ------------------------------------------------------------ Results */}
        <ReportSection
          id="results"
          title="Business results"
          description="Bookings, revenue and riders built from the validated data."
          lineage={<code>analytics.daily_city_summary</code>}
          actions={
            <Link className="section-link" to="/analytics">
              Business analytics <Icon name="arrowRight" size={14} />
            </Link>
          }
        >
          <AsyncView state={overview} skeletonRows={3}>
            {(o) => (
              <div className="grid kpis">
                <KpiCard label="Total bookings" value={formatInt(o.total_bookings)} hint={`${formatInt(o.completed_rides)} completed`} />
                <KpiCard label="Revenue" value={formatInrCompact(o.total_revenue_inr)} hint={formatInr(o.total_revenue_inr)} />
                <KpiCard label="Cancellation rate" value={formatPct(o.cancellation_rate)} hint="Rider, driver and system cancellations" />
                <KpiCard label="Active drivers" value={formatInt(o.active_drivers)} hint={`${formatInt(o.active_customers)} active riders`} />
                <KpiCard
                  label={`Cloud spend${o.cloud_spend.month ? `, ${o.cloud_spend.month}` : ""}`}
                  value={formatUsd(o.cloud_spend.amount_usd, 0)}
                  hint={<SourceBadge sources={o.cloud_spend.sources} />}
                />
              </div>
            )}
          </AsyncView>

          <AsyncView state={revenue} isEmpty={(r) => r.series.length === 0} empty="No completed rides in this period.">
            {(r) => {
              const rows = pivotByCity(r.series);
              const cities = CITY_ORDER.filter((c) => r.by_city.some((b) => b.city === c));
              const cityTable = (
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
                        <td>
                          <span className="cell-key">
                            <span className="swatch" style={{ background: cityColor(c.city) }} aria-hidden="true" />
                            {c.city_name}
                          </span>
                        </td>
                        <td className="num">{formatInt(c.bookings)}</td>
                        <td className="num">{formatInr(c.revenue_inr)}</td>
                        <td className="num">{formatInr(c.avg_fare_inr, 2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              );
              return (
                <div className="band-grid">
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
                    table={cityTable}
                  />
                  <ChartCard
                    title="Revenue by city"
                    subtitle="Circle area follows revenue; schematic positions"
                    height={340}
                    chart={<CityMap cities={r.by_city} />}
                    table={cityTable}
                  />
                </div>
              );
            }}
          </AsyncView>

          <Card title="Rider app by platform" subtitle="Sessions and how many ended in a booking (app telemetry, Spark lakehouse)">
            <AsyncView
              state={apps}
              skeletonRows={3}
              isEmpty={(a) => !a.available || !a.platforms?.length}
              empty="App telemetry appears after the Spark lakehouse export: python -m rideops.spark all."
            >
              {(a) => {
                const known = a.platforms.filter((p) => p.platform !== "unknown");
                const max = Math.max(...known.map((p) => p.sessions), 1);
                return (
                  <ul className="platform-list">
                    {known.map((p) => (
                      <li key={p.platform}>
                        <span className="platform-name">{p.platform === "ios" ? "iOS" : humanize(p.platform)}</span>
                        <div className="meter">
                          <span style={{ width: `${(p.sessions / max) * 100}%`, background: "var(--src-rider)" }} />
                        </div>
                        <span className="platform-figures">
                          <span className="num">{formatInt(p.sessions)} sessions</span>
                          <span className="num">{formatPct(p.conversion_rate)} booked</span>
                          <span className="num fact-note">{formatInt(p.crashes)} crashes</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                );
              }}
            </AsyncView>
          </Card>
        </ReportSection>
      </div>
    </>
  );
}
