import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { api, ApiError } from "../../api/client";
import { useApi } from "../../api/useApi";
import { AsyncView } from "../../components/AsyncView";
import { Card, KpiCard } from "../../components/Card";
import { ChartCard, ChartTooltip } from "../../components/ChartCard";
import { axisProps, gridProps } from "../../lib/charts";
import { formatDateTime, formatDuration } from "../../lib/format";
import { useLens } from "../LensContext";
import { ConnectionTabs } from "../components/ConnectionTabs";
import { StateBadge } from "../components/StateBadge";
import { UptimeBars } from "../components/UptimeBars";
import { ENGINE_LABEL, ENV_LABEL, formatBytes, timeAgo } from "../status";
import type { ConnectionStatus, HistoryPoint, TestResult, Uptime } from "../types";
import { StageList } from "./AddConnectionPage";
import { usePolling } from "./FleetPage";

// Connection overview (wireframes §7, §8): live state, score breakdown, uptime, latency, checks.

const POLL_MS = 15_000;

function metric(value: number | null | undefined, unit = "") {
  return value == null ? "–" : `${value}${unit}`;
}

export function ConnectionPage() {
  const { id = "" } = useParams();
  const { workspace, can } = useLens();
  const navigate = useNavigate();
  const ws = workspace?.workspace_id ?? "";
  const base = `/workspaces/${ws}/connections/${id}`;
  const [range, setRange] = useState<"24h" | "7d" | "30d">("24h");
  const status = useApi<ConnectionStatus>(ws ? `${base}/status` : null);
  const uptime = useApi<Uptime>(ws ? `${base}/uptime?window=${range}` : null);
  const history = useApi<HistoryPoint[]>(ws ? `${base}/status/history` : null);
  usePolling(status.reload, POLL_MS);
  usePolling(history.reload, POLL_MS);
  const [message, setMessage] = useState<{ tone: "good" | "critical"; text: string } | null>(null);
  const [retest, setRetest] = useState<TestResult | null>(null);

  async function act(label: string, run: () => Promise<unknown>) {
    setMessage(null);
    try {
      await run();
      setMessage({ tone: "good", text: label });
      status.reload();
      history.reload();
    } catch (err) {
      setMessage({ tone: "critical", text: (err as ApiError).message });
    }
  }

  return (
    <AsyncView state={status} skeletonRows={6}>
      {({ connection: c, latest, uptime_24h }) => {
        const m = latest?.metrics ?? {};
        const unavailable = latest?.unavailable ?? {};
        return (
          <>
            <ConnectionTabs />
            {c.environment === "prod" && (
              <div className="banner" data-tone="warning" role="note">Production database: Lens has read-only access.</div>
            )}
            <header className="page-header">
              <div>
                <h1 className="title-row">{c.name} <StateBadge state={c.current_state} />
                  {c.health_score != null && <span className="score">Score {c.health_score}</span>}</h1>
                <p>
                  {c.server_version ?? ENGINE_LABEL[c.engine]} · <span className="mono">{c.display_url}</span> ·{" "}
                  <span className="env-chip" data-env={c.environment}>{ENV_LABEL[c.environment]}</span>
                </p>
                <p className="muted">Checked {timeAgo(c.last_checked_at)} · every {c.check_interval_s / 60} min
                  {c.tags.length > 0 && <> · {c.tags.map((t) => <span key={t} className="tag">{t}</span>)}</>}</p>
              </div>
              {can("editor") && (
                <div className="toolbar">
                  <button className="btn" type="button" disabled={c.is_paused}
                          onClick={() => act("Check queued; results appear in a few seconds.", () => api.post(`${base}/status/check`))}>
                    ↻ Check now</button>
                  <button className="btn" type="button"
                          onClick={() => act("Connection test finished.", async () => setRetest(await api.post<TestResult>(`${base}/test`)))}>
                    Re-test</button>
                  <button className="btn" type="button"
                          onClick={() => act(c.is_paused ? "Monitoring resumed." : "Monitoring paused.",
                                             () => api.post(`${base}/${c.is_paused ? "resume" : "pause"}`))}>
                    {c.is_paused ? "Resume" : "Pause"}</button>
                  {can("admin") && !c.is_demo && (
                    <button className="btn danger" type="button" onClick={() => {
                      if (globalThis.confirm(`Delete ${c.name}? Its credentials are destroyed now; it can be restored within 7 days with new credentials.`)) {
                        void act("Deleted.", async () => { await api.delete(base); navigate("/lens"); });
                      }
                    }}>Delete</button>
                  )}
                </div>
              )}
            </header>
            {message && <p className={message.tone === "good" ? "notice" : "form-error"} role="status">{message.text}</p>}
            {retest && <Card title="Re-test result" actions={<button className="btn small" type="button" onClick={() => setRetest(null)}>Close</button>}>
              <StageList result={retest} /></Card>}
            {latest?.error_code && (
              <div className="banner" data-tone="critical" role="alert">
                <strong>{latest.error_code}</strong>: {latest.error_message}
              </div>
            )}

            <div className="stack">
              <div className="grid kpis">
                <KpiCard label="Latency" value={metric(latest?.latency_ms, " ms")} hint="last check" />
                <KpiCard label="Uptime 24 h" value={uptime_24h == null ? "–" : `${uptime_24h}%`}
                         tone={uptime_24h == null ? undefined : uptime_24h >= 99.5 ? "good" : uptime_24h >= 95 ? "warning" : "critical"} />
                <KpiCard label="Connections" value={m["connections.max"] ? `${m["connections.total"]} / ${m["connections.max"]}` : "–"}
                         hint={m["connections.used_pct"] != null ? `${m["connections.used_pct"]}% used` : undefined} />
                <KpiCard label="Database size" value={formatBytes(latest?.db_size_bytes)} />
                <KpiCard label="Cache hit" value={m["cache.hit_ratio"] != null ? `${(Number(m["cache.hit_ratio"]) * 100).toFixed(1)}%` : "–"} />
                <KpiCard label="Replication lag" value={m["replication.lag_s"] != null ? `${m["replication.lag_s"]} s` : "Primary"}
                         hint={m["replication.lag_s"] == null ? "not a replica" : undefined} />
              </div>

              <Card title="Uptime" actions={
                <div className="segmented" role="group" aria-label="Uptime window">
                  {(["24h", "7d", "30d"] as const).map((w) => (
                    <button key={w} type="button" aria-pressed={range === w} onClick={() => setRange(w)}>{w}</button>
                  ))}
                </div>}>
                <AsyncView state={uptime}>
                  {(u) => (<>
                    <p className="big-figure">{u.uptime_pct == null ? "–" : `${u.uptime_pct}%`} <span className="muted">over {u.checks} {u.checks === 1 ? "check" : "checks"}</span></p>
                    <UptimeBars uptime={u} />
                  </>)}
                </AsyncView>
              </Card>

              <div className="grid two">
                <Card title="Health score breakdown" subtitle="Start at 100 and subtract (design §15.2)">
                  {latest?.health_score == null ? <p className="muted">No score: the last check did not reach the database.</p> : (
                    <dl className="kv">
                      <dt>Start</dt><dd className="mono">100</dd>
                      {latest.score_breakdown.length === 0 && <><dt>No deductions</dt><dd className="mono">−0</dd></>}
                      {latest.score_breakdown.map((d) => (
                        <div key={d.component} style={{ display: "contents" }}>
                          <dt>{d.reason}</dt><dd className="mono">−{d.points}</dd>
                        </div>
                      ))}
                      <dt><strong>Score</strong></dt><dd className="mono"><strong>{latest.health_score}</strong></dd>
                    </dl>
                  )}
                </Card>
                <Card title="Activity" subtitle="From the last check (query text truncated and masked)">
                  <dl className="kv">
                    <dt>Active sessions</dt><dd className="mono">{metric(m["connections.active"])}</dd>
                    <dt>Idle sessions</dt><dd className="mono">{metric(m["connections.idle"])}</dd>
                    <dt>Long-running (&gt; 5 min)</dt><dd className="mono">{metric(m["queries.long_running"])}</dd>
                    <dt>Blocked sessions</dt><dd className="mono">{metric(m["locks.blocked"])}</dd>
                    <dt>Deadlocks since last check</dt><dd className="mono">{metric(m["deadlocks.delta"])}</dd>
                    <dt>Commits / s</dt><dd className="mono">{metric(m["tx.commits_per_s"])}</dd>
                    <dt>Transaction ID age</dt><dd className="mono">{metric(m["pg.xid_age_pct"], "%")}</dd>
                    <dt>Server uptime</dt><dd className="mono">{formatDuration(latest?.uptime_s)}</dd>
                  </dl>
                  {(latest?.details.long_running ?? []).length > 0 && (
                    <ul className="long-queries">
                      {latest?.details.long_running?.map((q) => (
                        <li key={q.pid}><span className="mono">pid {q.pid}</span> · {q.user} · {formatDuration(q.runtime_s)}
                          {q.query && <pre className="mono">{q.query}</pre>}</li>
                      ))}
                    </ul>
                  )}
                  {Object.entries(unavailable).map(([k, reason]) => (
                    <p key={k} className="hint">Not available: {k.replace(/\./g, " ")} ({reason})</p>
                  ))}
                </Card>
              </div>

              <AsyncView state={history} isEmpty={(h) => h.length === 0} empty="No checks yet. The first one runs within a minute of saving.">
                {(h) => (
                  <>
                    <ChartCard title="Latency" subtitle="Each health check, last 24 hours (ms)" height={220}
                      chart={
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={h} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid {...gridProps} />
                            <XAxis dataKey="at" {...axisProps} tickFormatter={(v: string) => new Date(v).toISOString().slice(11, 16)} minTickGap={32} />
                            <YAxis {...axisProps} width={44} />
                            <Tooltip content={({ active, payload }) => active && payload?.[0] ? (
                              <ChartTooltip title={formatDateTime(String(payload[0].payload.at))}
                                            rows={[{ label: "Latency", value: `${payload[0].value ?? "–"} ms` }]} />) : null} />
                            <Line type="monotone" dataKey="latency_ms" stroke="var(--series-1)" strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
                          </LineChart>
                        </ResponsiveContainer>
                      }
                      table={
                        <table>
                          <thead><tr><th scope="col">Time (UTC)</th><th scope="col">State</th><th scope="col" className="num">Latency</th><th scope="col" className="num">Score</th></tr></thead>
                          <tbody>
                            {[...h].reverse().slice(0, 100).map((p) => (
                              <tr key={p.at}><td>{formatDateTime(p.at)}</td><td>{p.state ? <StateBadge state={p.state} /> : "–"}</td>
                                <td className="num">{metric(p.latency_ms, " ms")}</td><td className="num">{metric(p.health_score)}</td></tr>
                            ))}
                          </tbody>
                        </table>
                      }
                    />
                  </>
                )}
              </AsyncView>
              {c.is_demo && <p className="table-note">This is the built-in RideOps demo database (read-only, synthetic data).</p>}
            </div>
          </>
        );
      }}
    </AsyncView>
  );
}
