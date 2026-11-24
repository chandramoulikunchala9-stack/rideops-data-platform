import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError, query } from "../../api/client";
import { useApi } from "../../api/useApi";
import { AsyncView } from "../../components/AsyncView";
import { Card } from "../../components/Card";
import { formatCompact } from "../../lib/format";
import { useLens } from "../LensContext";
import { ConnectionTabs } from "../components/ConnectionTabs";
import { formatBytes, timeAgo } from "../status";
import type { Catalog } from "../types";

/** Schema explorer (wireframes §10): schemas → tables, search across tables and columns. */
export function SchemaPage() {
  const { id = "" } = useParams();
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const [search, setSearch] = useState("");
  const [schema, setSchema] = useState("");
  const catalog = useApi<Catalog>(ws ? `/workspaces/${ws}/connections/${id}/catalog${query({ search, schema })}` : null);
  const [message, setMessage] = useState<string | null>(null);

  async function sync() {
    try {
      await api.post(`/workspaces/${ws}/connections/${id}/catalog/sync`);
      setMessage("Schema sync queued. Refresh in a few seconds.");
    } catch (err) {
      setMessage((err as ApiError).message);
    }
  }

  return (
    <>
      <ConnectionTabs />
      <header className="page-header">
        <div>
          <h1>Schema</h1>
          <p>Tables, views and columns from the catalog. Lens reads catalog views only; no table data.</p>
        </div>
        {can("editor") && <button className="btn" type="button" onClick={sync}>↻ Sync schema</button>}
      </header>
      {message && <p className="notice" role="status">{message}</p>}
      <Card>
        <div className="filters">
          <div className="field">
            <label htmlFor="s-search">Search tables and columns</label>
            <input id="s-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="s-schema">Schema</label>
            <select id="s-schema" value={schema} onChange={(e) => setSchema(e.target.value)}>
              <option value="">All schemas</option>
              {(catalog.data?.schemas ?? []).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        </div>
        <AsyncView state={catalog} isEmpty={(c) => c.tables.length === 0}
                   empty={catalog.data?.last_sync ? "No tables match." : "The schema hasn't been synced yet. It runs right after a connection is saved."}>
          {(c) => (
            <>
              <p className="table-note">
                {c.tables.length} objects · {formatBytes(c.total_bytes)} · last sync{" "}
                {c.last_sync ? `${timeAgo(c.last_sync.finished_at ?? c.last_sync.started_at)}${c.last_sync.ok === false ? " (failed)" : ""}` : "never"}
              </p>
              <div className="table-wrap">
                <table>
                  <thead><tr><th scope="col">Table</th><th scope="col">Kind</th><th scope="col" className="num">Rows (est.)</th>
                    <th scope="col" className="num">Size</th><th scope="col">Freshness</th><th scope="col">Notes</th></tr></thead>
                  <tbody>
                    {c.tables.map((t) => (
                      <tr key={`${t.schema_name}.${t.table_name}`}>
                        <td>
                          {t.is_pinned && <span aria-label="Pinned">📌 </span>}
                          <Link to={`/lens/connections/${id}/tables/${encodeURIComponent(t.schema_name)}/${encodeURIComponent(t.table_name)}`}>
                            <span className="muted">{t.schema_name}.</span>{t.table_name}
                          </Link>
                          {t.matching_columns.length > 0 && <div className="hint">columns: {t.matching_columns.join(", ")}</div>}
                        </td>
                        <td>{t.kind}</td>
                        <td className="num">{formatCompact(t.row_estimate)}</td>
                        <td className="num">{formatBytes(t.total_bytes)}</td>
                        <td>{t.freshness_column ? <span title={`${t.freshness_column}: ${t.freshness_max ?? "unknown"}`}>{timeAgo(t.freshness_max)}</span> : <span className="muted">–</span>}</td>
                        <td>
                          {!t.primary_key && t.kind === "table" && <span className="tag">no PK</span>}
                          {t.pii_columns.length > 0 && <span className="tag" title={t.pii_columns.join(", ")}>PII ×{t.pii_columns.length}</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </AsyncView>
      </Card>
    </>
  );
}
