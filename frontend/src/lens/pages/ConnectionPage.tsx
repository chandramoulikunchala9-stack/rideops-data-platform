import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { api, ApiError } from "../../api/client";
import { useApi } from "../../api/useApi";
import { AsyncView } from "../../components/AsyncView";
import { Card, ReportSection } from "../../components/Card";
import { ChartCard, ChartTooltip } from "../../components/ChartCard";
import { Icon } from "../../components/Icon";
import { axisProps, gridProps } from "../../lib/charts";
import { formatDateTime, formatDuration, formatInt } from "../../lib/format";
import { useLens } from "../LensContext";
import { ConfirmButton } from "../components/ConfirmButton";
import { ConnectionTabs } from "../components/ConnectionTabs";
import { StateBadge } from "../components/StateBadge";
import { UptimeBars } from "../components/UptimeBars";
import {
  ENGINE_SOURCES, ENV_LABEL, METRIC_CAPABILITY, STATE_META, engineLabel, formatBytes, formatSince, formatSpan, timeAgo,
} from "../status";
import type { Connection, ConnectionStatus, HistoryPoint, Snapshot, TestResult, Uptime } from "../types";
import { StageList } from "./AddConnectionPage";
import { usePolling } from "./FleetPage";

// Connection overview: status first (state, since when, score, uptime), then the health figures
// and activity, each section naming the engine views it was read from and when.

const POLL_MS = 15_000;
const WINDOWS = ["24h", "7d", "30d"] as const;
const COLLECTING = 12; // fewer checks than this and uptime does not mean much yet

function metric(value: number | null | undefined, unit = "") {
  return value == null ? "–" : `${formatInt(value)}${unit}`;
}

/** Why a figure is missing, or null when there is one to show (never a blank, never a guess).
 *  A value the check returned always wins; then the engine's own reason; then the capability list. */
function gate(c: Connection, latest: Snapshot | null, capability: string, unavailableKey: string, metricKey: string) {
  if (latest?.metrics?.[metricKey] != null) return null;
  const reason = latest?.unavailable?.[unavailableKey];
  if (reason) return reason;
  if (!c.capabilities.includes(capability)) return `Not available for ${engineLabel(c.engine)}`;
  return null;
}

function Lineage({ views, at }: { views: string[]; at: string | null | undefined }) {
  if (!views.length) return null;
  return (
    <>
      {views.map((v) => <code key={v}>{v}</code>)}
      <span>· read {timeAgo(at)}</span>
    </>
  );
}

export function ConnectionPage() {
  const { id = "" } = useParams();
  const { workspace, can } = useLens();
  const navigate = useNavigate();
  const ws = workspace?.workspace_id ?? "";
  const base = `/workspaces/${ws}/connections/${id}`;
  const [range, setRange] = useState<(typeof WINDOWS)[number]>("24h");
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
      {({ connection: c, latest }) => {
        const m = latest?.metrics ?? {};
        const meta = STATE_META[c.current_state] ?? STATE_META.unknown;
        const since = formatSince(c.last_state_change_at);
        const sources = ENGINE_SOURCES[c.engine] ?? { health: [], activity: [] };
        const sessionsNa = gate(c, latest, METRIC_CAPABILITY.sessions!, "connections", "connections.total");
        const cacheNa = gate(c, latest, METRIC_CAPABILITY.cache!, "cache.hit_ratio", "cache.hit_ratio");
        const replicationNa = gate(c, latest, METRIC_CAPABILITY.replication!, "replication", "replication.lag_s");
        const longNa = gate(c, latest, METRIC_CAPABILITY.long_queries!, "queries.long_running", "queries.long_running");
        const locksNa = gate(c, latest, METRIC_CAPABILITY.locks!, "locks.blocked", "locks.blocked");
        const lag = m["replication.lag_s"];
        if (c.is_demo) {
          try { localStorage.setItem("lens-demo-explored", "1"); } catch { /* the checklist just won't tick */ }
        }
        // Engine notes not already shown in place of a figure (e.g. query text needs a monitoring role).
        const shownNa = new Set(["connections", "cache.hit_ratio", "replication", "queries.long_running", "locks.blocked"]);
        const otherNotes = Object.entries(latest?.unavailable ?? {}).filter(([k]) => !shownNa.has(k));
        return (
          <>
            <ConnectionTabs showState={false} />
            <header className="page-header">
              <div>
                <h1>{c.name}</h1>
                <p className="conn-meta">
                  <span>{c.server_version ?? engineLabel(c.engine)}</span>
                  <span className="mono break-anywhere">{c.display_url}</span>
                  <span className="env-chip" data-env={c.environment}>{ENV_LABEL[c.environment]}</span>
                  {c.tags.map((t) => <span key={t} className="tag">{t}</span>)}
                </p>
                {c.environment === "prod" && (
                  <p className="conn-readonly"><Icon name="check" size={14} /> Production database: Lens reads only, never writes.</p>
                )}
              </div>
              {can("editor") && (
                <div className="toolbar">
                  <button className="btn" type="button" disabled={c.is_paused}
                          onClick={() => act("Check queued. The result appears here within a few seconds.", () => api.post(`${base}/status/check`))}>
                    <Icon name="refresh" size={15} /> Check now</button>
                  <button className="btn" type="button"
                          onClick={() => act("Connection test finished. Each stage is listed below.", async () => setRetest(await api.post<TestResult>(`${base}/test`)))}>
                    Re-test</button>
                  <button className="btn" type="button"
                          onClick={() => act(c.is_paused ? "Monitoring resumed." : "Monitoring paused. No checks run until you resume.",
                                             () => api.post(`${base}/${c.is_paused ? "resume" : "pause"}`))}>
                    {c.is_paused ? "Resume" : "Pause"}</button>
                  {can("admin") && !c.is_demo && (
                    <ConfirmButton label="Delete" confirmLabel={`Delete ${c.name}`}
                                   consequence="Credentials are destroyed now; restorable for 7 days with new ones."
                                   onConfirm={() => void act("Deleted.", async () => { await api.delete(base); navigate("/lens"); })} />
                  )}
                </div>
              )}
            </header>
            {message && (
              <p className={`notice ${message.tone === "good" ? "success" : "error"}`} role={message.tone === "good" ? "status" : "alert"}>
                {message.text}
              </p>
            )}
            {retest && <Card title="Re-test result" actions={<button className="btn small" type="button" onClick={() => setRetest(null)}>Close</button>}>
              <StageList result={retest} /></Card>}

            <div className="report">
              <section className="status-band" data-tone={meta.tone} aria-labelledby="status-title">
                <div className="status-now">
                  <h2 id="status-title" className="status-state">
                    <Icon name={meta.icon} size={26} /> {meta.label}
                  </h2>
                  <p className="status-since">
                    {since ? <>For {since}</> : <>No state change recorded yet</>}
                    {" · "}checked {timeAgo(c.last_checked_at)}, every {c.check_interval_s / 60} min
                  </p>
                  {latest?.error_message && (
                    <p className="status-error" role="alert">
                      {latest.error_message}
                      {latest.error_code && <> <code>{latest.error_code}</code></>}
                    </p>
                  )}
                </div>
                <div className="status-score">
                  <p className="status-score-value">
                    {c.health_score == null ? "–" : c.health_score}<span> / 100</span>
                  </p>
                  {latest?.health_score == null ? (
                    <p className="hint">No score: the last check did not reach the database.</p>
                  ) : latest.score_breakdown.length === 0 ? (
                    <p className="hint">Health score: nothing to deduct on the last check.</p>
                  ) : (
                    <ul className="deductions" aria-label="Score deductions">
                      {latest.score_breakdown.map((d) => (
                        <li key={d.component}><span>{d.reason}</span><span className="num">−{Math.abs(d.points)}</span></li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="status-uptime">
                  <div className="status-uptime-head">
                    <AsyncView state={uptime}>
                      {(u) => (
                        <p className="status-uptime-figure">
                          {u.checks < COLLECTING ? (
                            <>Collecting: {u.checks} {u.checks === 1 ? "check" : "checks"} so far</>
                          ) : (
                            <><strong>{u.uptime_pct == null ? "–" : `${u.uptime_pct}%`}</strong> uptime over {formatInt(u.checks)} checks</>
                          )}
                        </p>
                      )}
                    </AsyncView>
                    <div className="segmented" role="group" aria-label="Uptime window">
                      {WINDOWS.map((w) => (
                        <button key={w} type="button" aria-pressed={range === w} onClick={() => setRange(w)}>{w}</button>
                      ))}
                    </div>
                  </div>
                  {uptime.data && <UptimeBars uptime={uptime.data} />}
                </div>
              </section>

              <ReportSection id="health" title="Health" description="Figures from the last check."
                             lineage={<Lineage views={sources.health} at={latest?.taken_at} />}>
                <dl className="facts metrics">
                  <div><dt>Latency</dt><dd>{metric(latest?.latency_ms, " ms")}</dd></div>
                  <div><dt>Sessions</dt>
                    {sessionsNa ? <dd className="fact-na">{sessionsNa}</dd> : (
                      <dd><span>{m["connections.max"] ? `${formatInt(m["connections.total"])} / ${formatInt(m["connections.max"])}` : metric(m["connections.total"])}</span>
                        {m["connections.used_pct"] != null && <span className="fact-note"> {m["connections.used_pct"]}% of max</span>}</dd>)}</div>
                  <div><dt>Database size</dt><dd>{formatBytes(latest?.db_size_bytes)}</dd></div>
                  <div><dt>Cache hit</dt>
                    {cacheNa ? <dd className="fact-na">{cacheNa}</dd>
                      : <dd>{m["cache.hit_ratio"] != null ? `${(Number(m["cache.hit_ratio"]) * 100).toFixed(1)}%` : "–"}</dd>}</div>
                  <div><dt>Replication</dt>
                    {replicationNa ? <dd className="fact-na">{replicationNa}</dd>
                      : <dd>{lag != null ? `${formatInt(lag)} s behind` : <><span>Primary</span><span className="fact-note"> not a replica</span></>}</dd>}</div>
                  <div><dt>Server uptime</dt><dd>{formatSpan(latest?.uptime_s)}</dd></div>
                </dl>
              </ReportSection>

              {sources.activity.length > 0 && (
                <ReportSection id="activity" title="Activity" description="Sessions and queries at the last check. Query text is truncated and masked."
                               lineage={<Lineage views={sources.activity} at={latest?.taken_at} />}>
                  <dl className="kv activity">
                    <dt>Active sessions</dt><dd className="num">{sessionsNa ? <span className="fact-na">{sessionsNa}</span> : metric(m["connections.active"])}</dd>
                    <dt>Idle sessions</dt><dd className="num">{sessionsNa ? <span className="fact-na">{sessionsNa}</span> : metric(m["connections.idle"])}</dd>
                    <dt>Long-running (over 5 min)</dt><dd className="num">{longNa ? <span className="fact-na">{longNa}</span> : metric(m["queries.long_running"])}</dd>
                    <dt>Blocked sessions</dt><dd className="num">{locksNa ? <span className="fact-na">{locksNa}</span> : metric(m["locks.blocked"])}</dd>
                    <dt>Deadlocks since last check</dt><dd className="num">{metric(m["deadlocks.delta"])}</dd>
                    <dt>Commits per second</dt><dd className="num">{metric(m["tx.commits_per_s"])}</dd>
                    {c.engine === "postgres" && <><dt>Transaction ID age</dt><dd className="num">{metric(m["pg.xid_age_pct"], "%")}</dd></>}
                  </dl>
                  {otherNotes.map(([k, reason]) => (
                    <p key={k} className="hint">Not shown: {k.replace(/\./g, " ").replace(/_/g, " ")} ({reason})</p>
                  ))}
                  {(latest?.details.long_running ?? []).length > 0 && (
                    <ul className="long-queries">
                      {latest?.details.long_running?.map((q) => (
                        <li key={q.pid}><span className="mono">pid {q.pid}</span> · {q.user} · running {formatDuration(q.runtime_s)}
                          {q.query && <pre className="mono">{q.query}</pre>}</li>
                      ))}
                    </ul>
                  )}
                </ReportSection>
              )}

              <AsyncView state={history} isEmpty={(h) => h.length === 0} empty="No checks yet. The first one runs within a minute of saving.">
                {(h) => (
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
                          <Line type="monotone" dataKey="latency_ms" stroke="var(--series-1)" strokeWidth={2} dot={h.length < 3} connectNulls={false} isAnimationActive={false} />
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
                )}
              </AsyncView>
              {c.is_demo && <p className="table-note">This is the built-in demo warehouse: synthetic ride-hailing data, read-only. Its reports are under the Reports tab.</p>}
            </div>
          </>
        );
      }}
    </AsyncView>
  );
}
