import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError } from "../../api/client";
import { useApi } from "../../api/useApi";
import { AsyncView } from "../../components/AsyncView";
import { Card, KpiCard } from "../../components/Card";
import { formatCompact, formatDateTime } from "../../lib/format";
import { useLens } from "../LensContext";
import { ConnectionTabs } from "../components/ConnectionTabs";
import { BarList } from "../components/MiniCharts";
import { formatBytes, timeAgo } from "../status";
import type { Dashboard, SavedQuery, Widget } from "../types";

function WidgetView({ w }: { w: Widget }) {
  const r = w.result_cache;
  if (w.kind === "text") return <p>{w.options.text}</p>;
  if (!r) return <p className="muted">{w.last_error ? `Last refresh failed: ${w.last_error}` : "Not refreshed yet."}</p>;
  const names = r.columns.map((c) => c.name);
  const xi = Math.max(0, names.indexOf(w.options.x ?? names[0] ?? ""));
  const yi = names.indexOf(w.options.y ?? names[1] ?? "") >= 0 ? names.indexOf(w.options.y ?? names[1] ?? "") : Math.min(1, names.length - 1);
  if (w.kind === "kpi") return <p className="big-figure">{String(r.rows[0]?.[yi] ?? r.rows[0]?.[0] ?? "–")}</p>;
  if (w.kind === "bar" || w.kind === "line") {
    return <BarList label={w.title} bars={r.rows.slice(0, 30).map((row) => [row[xi], Number(row[yi]) || 0])} />;
  }
  return (
    <div className="table-wrap"><table className="data-grid">
      <thead><tr>{names.map((n) => <th key={n} scope="col">{n}</th>)}</tr></thead>
      <tbody>{r.rows.slice(0, 50).map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j} className="mono">{v === null ? "null" : String(v)}</td>)}</tr>)}</tbody>
    </table></div>
  );
}

export function DashboardsPage() {
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const list = useApi<Dashboard[]>(ws ? `/workspaces/${ws}/dashboards` : null);
  const [name, setName] = useState("");
  async function create(e: FormEvent) {
    e.preventDefault();
    await api.post(`/workspaces/${ws}/dashboards`, { name: name.trim() });
    setName(""); list.reload();
  }
  return (
    <>
      <header className="page-header"><div><h1>Dashboards</h1><p>Widgets built from saved queries, refreshed on demand or every 15 minutes or more.</p></div></header>
      {can("editor") && (
        <form className="input-row narrow" onSubmit={create}>
          <input aria-label="New dashboard name" placeholder="New dashboard name" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn primary" type="submit" disabled={!name.trim()}>Create</button>
        </form>
      )}
      <AsyncView state={list} isEmpty={(l) => l.length === 0} empty="No dashboards yet. Save a query in a connection's Query tab, then add it here.">
        {(items) => (
          <div className="grid two">
            {items.map((d) => (
              <Card key={d.id} title={d.name} subtitle={`${d.widget_count ?? 0} widgets · refreshed ${timeAgo(d.last_refreshed_at)}`}>
                <Link to={`/lens/dashboards/${d.id}`}>Open</Link>
              </Card>
            ))}
          </div>
        )}
      </AsyncView>
    </>
  );
}

export function DashboardPage() {
  const { dashboardId = "" } = useParams();
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const base = `/workspaces/${ws}/dashboards/${dashboardId}`;
  const dash = useApi<Dashboard>(ws ? base : null);
  const saved = useApi<SavedQuery[]>(ws && can("editor") ? `/workspaces/${ws}/saved-queries` : null);
  const [form, setForm] = useState({ kind: "bar", title: "", saved_query_id: "", x: "", y: "" });
  const [error, setError] = useState<string | null>(null);

  async function act(run: () => Promise<unknown>) {
    setError(null);
    try { await run(); dash.reload(); } catch (err) { setError((err as ApiError).message); }
  }
  return (
    <AsyncView state={dash} skeletonRows={4}>
      {(d) => (
        <>
          <header className="page-header">
            <div><p className="breadcrumb"><Link to="/lens/dashboards">Dashboards</Link> /</p><h1>{d.name}</h1>
              <p className="muted">Last refreshed {d.last_refreshed_at ? formatDateTime(d.last_refreshed_at) : "never"}</p></div>
            {can("editor") && <div className="toolbar">
              <button className="btn" type="button" onClick={() => act(() => api.post(`${base}/refresh`))}>↻ Refresh</button>
            </div>}
          </header>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="grid two">
            {(d.widgets ?? []).map((w) => (
              <Card key={w.id} title={w.title} subtitle={w.query_name ? `${w.query_name}${w.stale ? " · stale" : ""}` : undefined}
                    actions={can("editor") && <button className="btn small" type="button" onClick={() => act(() => api.delete(`${base}/widgets/${w.id}`))}>Remove</button>}>
                <WidgetView w={w} />
              </Card>
            ))}
          </div>
          {can("editor") && (
            <Card title="Add widget">
              <form className="form-grid" onSubmit={(e) => { e.preventDefault(); void act(() => api.post(`${base}/widgets`, {
                kind: form.kind, title: form.title.trim(), saved_query_id: form.saved_query_id || undefined,
                options: { x: form.x || undefined, y: form.y || undefined } })); }}>
                <div className="field"><label htmlFor="w-title">Title</label><input id="w-title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} required /></div>
                <div className="field"><label htmlFor="w-kind">Kind</label>
                  <select id="w-kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                    <option value="bar">Bar</option><option value="kpi">KPI</option><option value="table">Table</option>
                  </select></div>
                <div className="field span-2"><label htmlFor="w-query">Saved query</label>
                  <select id="w-query" value={form.saved_query_id} onChange={(e) => setForm({ ...form, saved_query_id: e.target.value })} required>
                    <option value="">Choose…</option>
                    {(saved.data ?? []).map((q) => <option key={q.id} value={q.id}>{q.name} ({q.connection_name})</option>)}
                  </select></div>
                <div className="field"><label htmlFor="w-x">Label column (optional)</label><input id="w-x" value={form.x} onChange={(e) => setForm({ ...form, x: e.target.value })} /></div>
                <div className="field"><label htmlFor="w-y">Value column (optional)</label><input id="w-y" value={form.y} onChange={(e) => setForm({ ...form, y: e.target.value })} /></div>
                <div className="span-2"><button className="btn primary" type="submit">Add widget</button></div>
              </form>
            </Card>
          )}
        </>
      )}
    </AsyncView>
  );
}

interface AutoDash {
  connection: { name: string; current_state: string; health_score: number | null };
  latency_7d: { at: string; latency_ms: number | null; uptime_pct: number }[];
  largest_tables: { schema_name: string; table_name: string; row_estimate: number | null; total_bytes: number | null }[];
  total_bytes: number;
  freshness: { schema_name: string; table_name: string; freshness_column: string; freshness_max: string | null; lag_s: number | null }[];
  insights: Partial<Record<"critical" | "warning" | "info", number>>;
  schema_changes_7d: number;
}

/** Auto dashboard per connection, built from Lens metadata only (no queries to the target). */
export function AutoDashboardPage() {
  const { id = "" } = useParams();
  const { workspace } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const data = useApi<AutoDash>(ws ? `/workspaces/${ws}/connections/${id}/dashboard` : null);
  return (
    <>
      <ConnectionTabs />
      <AsyncView state={data} skeletonRows={4}>
        {(d) => {
          const lat = d.latency_7d.map((p) => p.latency_ms).filter((v): v is number => v != null);
          const up = d.latency_7d.length ? d.latency_7d.reduce((s, p) => s + Number(p.uptime_pct), 0) / d.latency_7d.length : null;
          return (
            <div className="stack">
              <div className="grid kpis">
                <KpiCard label="Uptime (7 d)" value={up == null ? "–" : `${up.toFixed(2)}%`} />
                <KpiCard label="Avg latency (7 d)" value={lat.length ? `${Math.round(lat.reduce((a, b) => a + b, 0) / lat.length)} ms` : "–"} />
                <KpiCard label="Largest tables total" value={formatBytes(d.total_bytes)} />
                <KpiCard label="Open insights" value={(d.insights.critical ?? 0) + (d.insights.warning ?? 0) + (d.insights.info ?? 0)}
                         tone={d.insights.critical ? "critical" : d.insights.warning ? "warning" : undefined} />
                <KpiCard label="Schema changes (7 d)" value={d.schema_changes_7d} />
              </div>
              <div className="grid two">
                <Card title="Largest tables">
                  <BarList label="Largest tables by size" bars={d.largest_tables.map((t) => [`${t.schema_name}.${t.table_name}`, t.total_bytes ?? 0])} />
                  <p className="hint">Bytes; rows: {d.largest_tables.map((t) => `${t.table_name} ${formatCompact(t.row_estimate)}`).join(" · ")}</p>
                </Card>
                <Card title="Freshness" subtitle="Newest value of each table's timestamp column">
                  <ul className="plain-list">
                    {d.freshness.map((f) => (
                      <li key={`${f.schema_name}.${f.table_name}`} data-stale={(f.lag_s ?? 0) > 7 * 86400}>
                        <span className="mono">{f.schema_name}.{f.table_name}</span> · {timeAgo(f.freshness_max)}{(f.lag_s ?? 0) > 7 * 86400 && " ⚠ stale"}
                      </li>
                    ))}
                    {d.freshness.length === 0 && <li className="muted">No timestamp columns found yet.</li>}
                  </ul>
                </Card>
              </div>
            </div>
          );
        }}
      </AsyncView>
    </>
  );
}
