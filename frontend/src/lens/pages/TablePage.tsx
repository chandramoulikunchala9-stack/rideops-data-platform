import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError, apiUrl } from "../../api/client";
import { useApi } from "../../api/useApi";
import { AsyncView } from "../../components/AsyncView";
import { Card, KpiCard } from "../../components/Card";
import { formatCompact, formatDateTime, formatInt } from "../../lib/format";
import { useLens } from "../LensContext";
import { ConnectionTabs } from "../components/ConnectionTabs";
import { AutoChart } from "../components/MiniCharts";
import { formatBytes, timeAgo } from "../status";
import type { ProfileResponse, TableDetail } from "../types";

type Tab = "overview" | "columns" | "profile" | "preview" | "changes";

function fmt(value: unknown): string {
  if (value === null || value === undefined) return "–";
  if (typeof value === "number") return Number.isInteger(value) ? formatInt(value) : value.toFixed(3);
  return String(value);
}

function Preview({ base }: { base: string }) {
  const { can } = useLens();
  const [reveal, setReveal] = useState(false);
  const data = useApi<{ columns: string[]; rows: unknown[][]; masked_columns: string[] }>(`${base}/preview${reveal ? "?reveal=true" : ""}`);
  return (
    <Card title="Preview" subtitle="First 50 rows. Personal-data columns are masked unless revealed (the reveal is logged)."
          actions={can("editor") && (
            <label className="radio-row"><input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} /> Reveal PII</label>)}>
      <AsyncView state={data} isEmpty={(d) => d.rows.length === 0} empty="The table is empty.">
        {(d) => (
          <>
            {d.masked_columns.length > 0 && <p className="hint">Masked: {d.masked_columns.join(", ")}</p>}
            <div className="table-wrap">
              <table className="data-grid">
                <thead><tr>{d.columns.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
                <tbody>{d.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className="mono">{v === null ? <span className="muted">null</span> : typeof v === "object" ? JSON.stringify(v) : String(v)}</td>)}</tr>)}</tbody>
              </table>
            </div>
          </>
        )}
      </AsyncView>
    </Card>
  );
}

function Profile({ base }: { base: string }) {
  const { can } = useLens();
  const state = useApi<ProfileResponse>(`${base}/profile`);
  const [error, setError] = useState<string | null>(null);
  const { reload, data } = state;
  useEffect(() => {
    if (!data?.pending) return;
    const t = window.setTimeout(reload, 2000); // poll while the job runs
    return () => window.clearTimeout(t);
  }, [data, reload]);

  async function run() {
    setError(null);
    try {
      await api.post(`${base}/profile`);
      reload();
    } catch (err) {
      setError((err as ApiError).message);
    }
  }

  return (
    <Card title="Column profile" subtitle="Computed on a sample, never the whole table."
          actions={can("editor") && <button className="btn" type="button" onClick={run} disabled={data?.pending}>
            {data?.pending ? "Profiling…" : data?.profile ? "Re-profile" : "Profile table"}</button>}>
      {error && <p className="form-error" role="alert">{error}</p>}
      <AsyncView state={state} isEmpty={(d) => !d.profile} empty={data?.pending ? "Profiling…" : "No profile yet."}>
        {(d) => d.profile && (
          <div className="stack">
            <p className="table-note">{formatInt(d.profile.sample_rows)} sampled rows · {d.profile.method} · {formatDateTime(d.profile.profiled_at)}
              {" · "}<a href={apiUrl(`${base}/profile/export.json`)}>Export JSON</a></p>
            <div className="profile-grid">
              {d.profile.profiles.map((p) => {
                const chart = d.profile?.charts.find((c) => c.column === p.column);
                return (
                  <section key={p.column} className="profile-card card" aria-label={`Profile of ${p.column}`}>
                    <h3 className="mono">{p.column} {p.pii && <span className="tag">PII</span>}</h3>
                    <p className="hint">{p.data_type} · {p.kind}</p>
                    <dl className="kv compact">
                      <dt>Null</dt><dd>{p.null_pct ?? "–"}%</dd>
                      <dt>Distinct (sample)</dt><dd>{formatCompact(p.distinct_est)}</dd>
                      {p.min !== undefined && <><dt>Min</dt><dd className="mono">{fmt(p.min)}</dd></>}
                      {p.max !== undefined && <><dt>Max</dt><dd className="mono">{fmt(p.max)}</dd></>}
                      {p.mean !== undefined && <><dt>Mean</dt><dd className="mono">{fmt(p.mean)}</dd></>}
                      {p.length && <><dt>Length</dt><dd className="mono">{p.length.min}–{p.length.max}</dd></>}
                    </dl>
                    {chart && <AutoChart spec={chart} />}
                  </section>
                );
              })}
            </div>
          </div>
        )}
      </AsyncView>
    </Card>
  );
}

export function TablePage() {
  const { id = "", schema = "", table = "" } = useParams();
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const base = `/workspaces/${ws}/connections/${id}/tables/${encodeURIComponent(schema)}/${encodeURIComponent(table)}`;
  const detail = useApi<TableDetail>(ws ? base : null);
  const [tab, setTab] = useState<Tab>("overview");
  const [count, setCount] = useState<string | null>(null);

  async function exactCount() {
    try {
      const r = await api.post<{ row_exact: number }>(`${base}/count`);
      setCount(formatInt(r.row_exact));
    } catch (err) {
      setCount((err as ApiError).message);
    }
  }

  return (
    <>
      <ConnectionTabs />
      <AsyncView state={detail} skeletonRows={5}>
        {(d) => (
          <>
            <header className="page-header">
              <div>
                <p className="breadcrumb"><Link to={`/lens/connections/${id}/schema`}>Schema</Link> /</p>
                <h1 className="mono">{schema}.{table}</h1>
                <p>{d.table.kind}{d.table.comment ? ` · ${d.table.comment}` : ""}</p>
              </div>
            </header>
            <div className="segmented tabs" role="tablist" aria-label="Table sections">
              {(["overview", "columns", "profile", ...(can("editor") ? ["preview"] : []), "changes"] as Tab[]).map((t) => (
                <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
                  {t[0]!.toUpperCase() + t.slice(1)}</button>
              ))}
            </div>
            {tab === "overview" && (
              <div className="stack">
                <div className="grid kpis">
                  <KpiCard label="Rows (estimate)" value={formatCompact(d.stats?.row_estimate)}
                           hint={count ? `exact: ${count}` : can("editor") ? <button className="btn small" type="button" onClick={exactCount}>Exact count</button> : undefined} />
                  <KpiCard label="Total size" value={formatBytes(d.stats?.total_bytes)} hint={`indexes ${formatBytes(d.stats?.index_bytes)}`} />
                  <KpiCard label="Freshness" value={d.table.freshness_column ? timeAgo(d.stats?.freshness_max) : "–"}
                           hint={d.table.freshness_column ? `max(${d.table.freshness_column})` : "no timestamp column"} />
                  <KpiCard label="Dead rows" value={formatCompact(d.stats?.dead_rows)} hint={`last vacuum ${timeAgo(d.stats?.last_vacuum)}`} />
                </div>
                <div className="grid two">
                  <Card title="Relationships">
                    <ul className="plain-list">
                      {d.table.foreign_keys.map((fk) => <li key={fk.name}><span className="mono">{fk.columns.join(", ")}</span> → <span className="mono">{fk.ref_table}</span></li>)}
                      {d.referenced_by.map((r) => <li key={`${r.schema_name}.${r.table_name}`}>← <span className="mono">{r.schema_name}.{r.table_name}</span> ({r.fk.columns.join(", ")})</li>)}
                      {d.table.foreign_keys.length + d.referenced_by.length === 0 && <li className="muted">No foreign keys.</li>}
                    </ul>
                  </Card>
                  <Card title="Indexes">
                    <ul className="plain-list">
                      {d.table.indexes.map((i) => <li key={i.name}><span className="mono">{i.name}</span> ({i.columns.join(", ")}) · {formatBytes(i.bytes)} · {formatInt(i.scans)} scans{i.primary ? " · PK" : i.unique ? " · unique" : ""}</li>)}
                      {d.table.indexes.length === 0 && <li className="muted">No indexes.</li>}
                    </ul>
                  </Card>
                </div>
              </div>
            )}
            {tab === "columns" && (
              <Card>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th scope="col">#</th><th scope="col">Column</th><th scope="col">Type</th><th scope="col">Nullable</th><th scope="col">Notes</th></tr></thead>
                    <tbody>{d.table.columns.map((c) => (
                      <tr key={c.name}><td className="num">{c.ordinal}</td><td className="mono">{c.name}</td><td className="mono">{c.data_type}</td>
                        <td>{c.nullable ? "yes" : "no"}</td>
                        <td>{c.is_pk && <span className="tag">PK</span>}{c.pii && <span className="tag">PII</span>}{c.name === d.table.freshness_column && <span className="tag">freshness</span>}</td></tr>
                    ))}</tbody>
                  </table>
                </div>
              </Card>
            )}
            {tab === "profile" && <Profile base={base} />}
            {tab === "preview" && <Preview base={base} />}
            {tab === "changes" && (
              <Card title="Schema changes">
                {d.changes.length === 0 ? <p className="muted">No changes since the first sync.</p> : (
                  <ul className="plain-list">{d.changes.map((c, i) => <li key={i}>{formatDateTime(c.detected_at)} · <strong>{c.change_type.replace(/_/g, " ")}</strong> <span className="mono">{c.object_ref}</span></li>)}</ul>
                )}
              </Card>
            )}
          </>
        )}
      </AsyncView>
    </>
  );
}
