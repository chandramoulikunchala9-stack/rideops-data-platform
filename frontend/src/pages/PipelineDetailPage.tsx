import { useState } from "react";
import Markdown from "react-markdown";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { query } from "../api/client";
import type { Page, PipelineSummary, Run, RunDetail } from "../api/types";
import { useApi } from "../api/useApi";
import { AsyncView } from "../components/AsyncView";
import { Card } from "../components/Card";
import { ChartCard, ChartTooltip } from "../components/ChartCard";
import { Pagination } from "../components/Filters";
import { StatusBadge } from "../components/StatusBadge";
import { axisProps, BAR_RADIUS, gridProps, seriesColor } from "../lib/charts";
import { formatDateTime, formatDuration, formatInt } from "../lib/format";
import { RetryButton } from "./PipelinesPage";

interface TooltipLike {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: Run }>;
}

const PAGE_SIZE = 15;

export function PipelineDetailPage() {
  const { name = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);

  const runs = useApi<Page<Run>>(`/pipelines/${name}${query({ status, limit: PAGE_SIZE, offset })}`);
  const recent = useApi<Page<Run>>(`/pipelines/${name}?limit=40`);
  const selectedId = params.get("run") ?? runs.data?.items[0]?.run_id ?? null;
  const detail = useApi<RunDetail>(selectedId ? `/runs/${selectedId}` : null);
  const summaries = useApi<PipelineSummary[]>("/pipelines");
  const retryable = summaries.data?.find((p) => p.pipeline_name === name)?.retryable ?? false;

  const select = (runId: string) => setParams({ run: runId });
  const durations = [...(recent.data?.items ?? [])].reverse();

  return (
    <>
      <header className="page-header">
        <div>
          <p style={{ margin: 0 }}><Link to="/pipelines">← Pipelines</Link></p>
          <h1>{name}</h1>
          <p>Execution history, logs, failures and resolution notes</p>
        </div>
        <div className="toolbar">
          {retryable ? (
            <RetryButton pipeline={name} onDone={(text) => { setNotice(text); setTimeout(() => { runs.reload(); recent.reload(); }, 3000); }} />
          ) : (
            <span className="badge" title="Re-run with the Spark CLI or as a Databricks job">Spark / Databricks job</span>
          )}
        </div>
      </header>
      {notice && <div className="notice" role="status" style={{ marginBottom: 16 }}>{notice}</div>}

      <div className="stack">
        <AsyncView state={recent} isEmpty={(r) => r.items.length === 0}>
          {() => (
            <ChartCard
              title="Run duration timeline"
              subtitle="Last 40 runs, oldest to newest. Faded bars are failed or cancelled runs (exact status in the tooltip and table). Click a bar to inspect it."
              chart={
                <ResponsiveContainer>
                  <BarChart data={durations} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                    onClick={(e) => { const id = (e as { activeLabel?: string }).activeLabel; if (id) select(id); }}>
                    <CartesianGrid {...gridProps} />
                    <XAxis dataKey="run_id" {...axisProps} tick={false} />
                    <YAxis tickFormatter={(s: number) => formatDuration(s)} width={64} {...axisProps} />
                    <Tooltip
                      cursor={{ fill: "var(--surface-2)" }}
                      content={(p) => {
                        const run = (p as unknown as TooltipLike).payload?.[0]?.payload;
                        if (!(p as unknown as TooltipLike).active || !run) return null;
                        return (
                          <ChartTooltip title={formatDateTime(run.start_ts)} rows={[
                            { label: "Status", value: run.status },
                            { label: "Duration", value: formatDuration(run.duration_sec) },
                            { label: "Trigger", value: run.trigger },
                          ]} />
                        );
                      }}
                    />
                    <Bar dataKey="duration_sec" maxBarSize={14} radius={BAR_RADIUS} isAnimationActive={false} cursor="pointer">
                      {durations.map((run) => (
                        // One series colour (duration is the measure). Non-successful runs are faded;
                        // the status itself is spelled out in the tooltip and the table view.
                        <Cell key={run.run_id} fill={seriesColor(1)} fillOpacity={run.status === "SUCCESS" ? 1 : 0.35} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              }
              table={
                <table>
                  <thead><tr><th scope="col">Started</th><th scope="col">Status</th><th scope="col" className="num">Duration</th></tr></thead>
                  <tbody>
                    {durations.map((r) => (
                      <tr key={r.run_id}><td>{formatDateTime(r.start_ts)}</td><td><StatusBadge status={r.status} /></td><td className="num">{formatDuration(r.duration_sec)}</td></tr>
                    ))}
                  </tbody>
                </table>
              }
            />
          )}
        </AsyncView>

        <div className="split">
          <Card
            title="Execution history"
            actions={
              <label className="field">
                Status
                <select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
                  <option value="">Any</option>
                  {["SUCCESS", "FAILED", "CANCELLED", "RUNNING"].map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
            }
          >
            <AsyncView state={runs} isEmpty={(r) => r.items.length === 0} empty="No runs with this status.">
              {(r) => (
                <>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr><th scope="col">Started</th><th scope="col">Status</th><th scope="col">Trigger</th><th scope="col" className="num">Duration</th><th scope="col" className="num">Read</th><th scope="col" className="num">Rejected</th></tr>
                      </thead>
                      <tbody>
                        {r.items.map((run) => (
                          <tr key={run.run_id} className={run.run_id === selectedId ? "selected" : ""}>
                            <td>
                              <button type="button" className="btn small" style={{ border: 0, padding: 0, background: "none", color: "var(--accent-text)" }}
                                onClick={() => select(run.run_id)} aria-pressed={run.run_id === selectedId}>
                                {formatDateTime(run.start_ts)}
                              </button>
                            </td>
                            <td><StatusBadge status={run.status} /></td>
                            <td>{run.trigger}{run.retry_count > 0 && ` #${run.retry_count}`}</td>
                            <td className="num">{formatDuration(run.duration_sec)}</td>
                            <td className="num">{formatInt(run.records_read)}</td>
                            <td className="num">{formatInt(run.records_rejected)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <Pagination total={r.total} limit={r.limit} offset={r.offset} onChange={setOffset} />
                </>
              )}
            </AsyncView>
          </Card>

          <Card title="Run details" subtitle={selectedId ?? undefined}>
            <AsyncView state={detail} empty="Select a run.">
              {(d) => (
                <div className="stack">
                  <dl className="kv">
                    <dt>Status</dt><dd><StatusBadge status={d.run.status} /></dd>
                    <dt>Started</dt><dd>{formatDateTime(d.run.start_ts)}</dd>
                    <dt>Ended</dt><dd>{formatDateTime(d.run.end_ts)}</dd>
                    <dt>Duration</dt><dd>{formatDuration(d.run.duration_sec)}</dd>
                    <dt>Input / output</dt>
                    <dd>{formatInt(d.run.records_read)} read · {formatInt(d.run.records_written)} written · {formatInt(d.run.records_rejected)} rejected</dd>
                    <dt>Trigger</dt><dd>{d.run.trigger}{d.run.retry_of_run_id && ` (retry of ${d.run.retry_of_run_id})`}</dd>
                    <dt>Source</dt><dd>{d.run.run_source === "simulated_history" ? "Simulated history" : "Real local run"}</dd>
                  </dl>
                  {d.run.error_message && (
                    <div>
                      <strong>Failure</strong>
                      <div className="errorbox mono" role="alert">{d.run.error_message}</div>
                    </div>
                  )}
                  <div>
                    <strong>Logs</strong>
                    {d.logs.length === 0 ? (
                      <p style={{ color: "var(--text-muted)", margin: "4px 0" }}>No log lines were captured for this run.</p>
                    ) : (
                      <div className="logs mono">
                        {d.logs.map((line, i) => (
                          <div key={i}><span>{line.logged_at.slice(11, 19)}</span><span>{line.level}</span><span>{line.message}</span></div>
                        ))}
                      </div>
                    )}
                  </div>
                  {d.incidents.map((incident) => (
                    <div key={incident.incident_id}>
                      <div className="toolbar"><strong>{incident.incident_id}</strong><StatusBadge status={incident.status} /><span>{incident.severity}</span></div>
                      <p style={{ margin: "4px 0" }}>{incident.title}</p>
                      <details>
                        <summary>Resolution notes</summary>
                        <div className="markdown"><Markdown>{incident.resolution ?? ""}</Markdown></div>
                        {incident.runbook && <p>Runbook: <code>{incident.runbook}</code></p>}
                      </details>
                    </div>
                  ))}
                </div>
              )}
            </AsyncView>
          </Card>
        </div>
      </div>
    </>
  );
}
