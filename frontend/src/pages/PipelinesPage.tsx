import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { PipelineSummary, RetryAccepted } from "../api/types";
import { useApi } from "../api/useApi";
import { AsyncView } from "../components/AsyncView";
import { Card } from "../components/Card";
import { SourceBadge, StatusBadge } from "../components/StatusBadge";
import { formatDuration, formatPct, relativeTime } from "../lib/format";

const LAYERS = ["all", "bronze", "silver", "gold", "serving", "ops"];

export function RetryButton({ pipeline, onDone }: { pipeline: string; onDone: (message: string, ok: boolean) => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className="btn small"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          const result = await api.post<RetryAccepted>(`/pipelines/${pipeline}/retry`);
          onDone(result.message, true);
        } catch (error) {
          onDone(error instanceof ApiError ? error.message : "Retry failed", false);
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? "Starting…" : "Retry"}
    </button>
  );
}

export function PipelinesPage() {
  const pipelines = useApi<PipelineSummary[]>("/pipelines");
  const [search, setSearch] = useState("");
  const [layer, setLayer] = useState("all");
  const [status, setStatus] = useState("all");
  const [notice, setNotice] = useState<{ text: string; ok: boolean } | null>(null);

  const filtered = useMemo(
    () =>
      (pipelines.data ?? []).filter(
        (p) =>
          p.pipeline_name.includes(search.trim().toLowerCase()) &&
          (layer === "all" || p.layer === layer) &&
          (status === "all" || p.last_status === status),
      ),
    [pipelines.data, search, layer, status],
  );

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Pipelines</h1>
          <p>Latest status, reliability and duration of every data pipeline</p>
        </div>
        <div className="toolbar">
          <label className="field">
            Search
            <input type="search" value={search} placeholder="pipeline name" onChange={(e) => setSearch(e.target.value)} />
          </label>
          <label className="field">
            Layer
            <select value={layer} onChange={(e) => setLayer(e.target.value)}>
              {LAYERS.map((l) => <option key={l} value={l}>{l === "all" ? "All layers" : l}</option>)}
            </select>
          </label>
          <label className="field">
            Last status
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              {["all", "SUCCESS", "FAILED", "RUNNING", "CANCELLED"].map((s) => (
                <option key={s} value={s}>{s === "all" ? "Any status" : s}</option>
              ))}
            </select>
          </label>
          <button type="button" className="btn" onClick={pipelines.reload}>Refresh</button>
        </div>
      </header>

      {notice && (
        <div className={`notice ${notice.ok ? "success" : "error"}`} role="status" style={{ marginBottom: 16 }}>
          {notice.text}
        </div>
      )}

      <Card title="All pipelines" subtitle="Success rate over the last 30 days. Retry runs the pipeline for real (local pipelines only).">
        <AsyncView state={pipelines} isEmpty={() => filtered.length === 0} empty="No pipelines match the filters.">
          {() => (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Pipeline</th>
                    <th scope="col">Layer</th>
                    <th scope="col">Last run</th>
                    <th scope="col" className="num">Duration</th>
                    <th scope="col" className="num">Median</th>
                    <th scope="col" className="num">Success (30d)</th>
                    <th scope="col">Source</th>
                    <th scope="col"><span className="skip-link">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((p) => (
                    <tr key={p.pipeline_name}>
                      <td>
                        <Link to={`/pipelines/${p.pipeline_name}`}><strong>{p.pipeline_name}</strong></Link>
                        {p.last_error && p.last_status === "FAILED" && (
                          <div style={{ fontSize: 12, color: "var(--status-critical-text)" }}>{p.last_error.slice(0, 80)}…</div>
                        )}
                      </td>
                      <td>{p.layer}</td>
                      <td>
                        <StatusBadge status={p.last_status} />
                        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{relativeTime(p.last_run_at)}</div>
                      </td>
                      <td className="num">{formatDuration(p.last_duration_sec)}</td>
                      <td className="num">{formatDuration(p.p50_duration_sec)}</td>
                      <td className="num">{formatPct(p.success_rate)}<div style={{ fontSize: 12, color: "var(--text-muted)" }}>{p.runs} runs</div></td>
                      <td>
                        {p.run_sources.includes("local_run") ? (
                          <span className="badge" title="Includes runs executed on this machine">Real runs</span>
                        ) : (
                          <SourceBadge sources={p.run_sources} />
                        )}
                      </td>
                      <td>
                        {p.retryable ? (
                          <RetryButton pipeline={p.pipeline_name} onDone={(text, ok) => { setNotice({ text, ok }); if (ok) setTimeout(pipelines.reload, 3000); }} />
                        ) : (
                          <span style={{ fontSize: 12, color: "var(--text-muted)" }} title="Runs on Spark / Databricks, not the local job runner">Spark job</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncView>
      </Card>
    </>
  );
}
