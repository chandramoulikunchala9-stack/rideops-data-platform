import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { api, ApiError, apiUrl } from "../../api/client";
import { useApi } from "../../api/useApi";
import { Card } from "../../components/Card";
import { formatInt } from "../../lib/format";
import { useLens } from "../LensContext";
import { ConnectionTabs } from "../components/ConnectionTabs";
import type { QueryResult, SavedQuery } from "../types";

interface Verdict { ok: boolean; kind: string | null; code: string | null; message: string | null; tables: string[] }

/** Read-only query console (wireframes §13): guard verdict while typing, capped results, CSV export. */
export function QueryPage() {
  const { id = "" } = useParams();
  const { workspace } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const base = `/workspaces/${ws}/connections/${id}`;
  const saved = useApi<SavedQuery[]>(ws ? `/workspaces/${ws}/saved-queries?connection_id=${id}` : null);
  const [sqlText, setSql] = useState("SELECT now();");
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [plan, setPlan] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [maxRows, setMaxRows] = useState(1000);
  const [reveal, setReveal] = useState(false);
  const [saveName, setSaveName] = useState("");

  useEffect(() => { // debounce the guard check while typing
    const t = window.setTimeout(() => {
      if (!sqlText.trim() || !ws) return;
      api.post<Verdict>(`${base}/queries/validate`, { statement: sqlText }).then(setVerdict).catch(() => setVerdict(null));
    }, 400);
    return () => window.clearTimeout(t);
  }, [sqlText, base, ws]);

  async function run() {
    setBusy(true); setError(null); setPlan(null);
    try {
      setResult(await api.post<QueryResult>(`${base}/queries/run`, { statement: sqlText, max_rows: maxRows, reveal }));
    } catch (err) {
      setResult(null); setError((err as ApiError).message);
    } finally { setBusy(false); }
  }
  async function explain() {
    setBusy(true); setError(null);
    try {
      setPlan((await api.post<{ plan: unknown }>(`${base}/queries/explain`, { statement: sqlText })).plan);
    } catch (err) { setError((err as ApiError).message); } finally { setBusy(false); }
  }
  async function save() {
    try {
      await api.post(`/workspaces/${ws}/saved-queries`, { connection_id: id, name: saveName.trim(), statement: sqlText });
      setSaveName(""); saved.reload();
    } catch (err) { setError((err as ApiError).message); }
  }

  return (
    <>
      <ConnectionTabs />
      <header className="page-header"><div><h1>Query</h1><p>One read-only statement at a time: SELECT, WITH, EXPLAIN or SHOW. Results are capped and never stored.</p></div></header>
      <div className="query-layout">
        <div className="stack">
          <Card>
            <label htmlFor="sql" className="sr-only">SQL</label>
            <textarea id="sql" className="sql-editor mono" rows={8} spellCheck={false} value={sqlText} onChange={(e) => setSql(e.target.value)}
                      onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); void run(); } }}
                      aria-describedby="sql-verdict" />
            <p id="sql-verdict" className={verdict && !verdict.ok ? "form-error" : "hint"} role="status">
              {verdict ? (verdict.ok ? `✔ Read-only ${verdict.kind}${verdict.tables.length ? ` · reads ${verdict.tables.join(", ")}` : ""}` : `✗ ${verdict.code}: ${verdict.message}`) : "Ctrl/⌘ + Enter runs the query."}
            </p>
            <div className="toolbar wrap">
              <button className="btn primary" type="button" onClick={run} disabled={busy || verdict?.ok === false}>{busy ? "Running…" : "▶ Run"}</button>
              <button className="btn" type="button" onClick={explain} disabled={busy || verdict?.kind !== "select"}>Explain</button>
              <label className="radio-row">Max rows
                <select value={maxRows} onChange={(e) => setMaxRows(Number(e.target.value))}>
                  {[100, 1000, 5000, 10000].map((n) => <option key={n} value={n}>{formatInt(n)}</option>)}
                </select></label>
              <label className="radio-row"><input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} /> Reveal PII (logged)</label>
            </div>
          </Card>
          {error && <p className="form-error" role="alert">{error}</p>}
          {plan !== null && <Card title="Plan"><pre className="mono plan">{JSON.stringify(plan, null, 2)}</pre></Card>}
          {result && (
            <Card title={`${formatInt(result.row_count)} rows`} subtitle={`${result.elapsed_ms} ms${result.truncated ? " · truncated at the row cap" : ""}${result.masked_columns.length ? ` · masked: ${result.masked_columns.join(", ")}` : ""}`}
                  actions={<a className="btn small" href={apiUrl(`/workspaces/${ws}/query-runs/${result.run_id}/export.csv`)}>Export CSV</a>}>
              <div className="table-wrap result-grid">
                <table className="data-grid">
                  <thead><tr>{result.columns.map((c) => <th key={c.name} scope="col">{c.name}<span className="hint"> {c.type}</span></th>)}</tr></thead>
                  <tbody>{result.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className="mono">{v === null ? <span className="muted">null</span> : typeof v === "object" ? JSON.stringify(v) : String(v)}</td>)}</tr>)}</tbody>
                </table>
              </div>
            </Card>
          )}
        </div>
        <aside className="stack">
          <Card title="Save query">
            <div className="input-row">
              <input aria-label="Saved query name" placeholder="Name" value={saveName} onChange={(e) => setSaveName(e.target.value)} />
              <button className="btn small" type="button" onClick={save} disabled={!saveName.trim() || verdict?.ok === false}>Save</button>
            </div>
          </Card>
          <Card title="Saved queries">
            <ul className="plain-list">
              {(saved.data ?? []).map((q) => (
                <li key={q.id}><button className="link-button" type="button" onClick={() => setSql(q.statement)}>{q.name}</button></li>
              ))}
              {saved.data?.length === 0 && <li className="muted">None yet.</li>}
            </ul>
          </Card>
        </aside>
      </div>
    </>
  );
}
