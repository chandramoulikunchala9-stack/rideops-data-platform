import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError, query } from "../../api/client";
import { useApi } from "../../api/useApi";
import { AsyncView } from "../../components/AsyncView";
import { Card } from "../../components/Card";
import { formatDateTime } from "../../lib/format";
import { useLens } from "../LensContext";
import { ConnectionTabs } from "../components/ConnectionTabs";
import { timeAgo } from "../status";
import type { AlertEvent, AlertRule, Channel, Incident, NotificationList, Severity } from "../types";
import { usePolling } from "./FleetPage";

const SEV_TONE: Record<Severity, "critical" | "warning" | "neutral"> = { critical: "critical", warning: "warning", info: "neutral" };
const STATUS_TONE: Record<string, "critical" | "warning" | "good" | "neutral"> = {
  firing: "critical", acknowledged: "warning", muted: "neutral", resolved: "good", open: "critical",
};

function Sev({ s }: { s: Severity }) {
  return <span className="badge" data-tone={SEV_TONE[s]}>{s}</span>;
}

function Status({ s }: { s: string }) {
  return <span className="badge" data-tone={STATUS_TONE[s] ?? "neutral"}>{s}</span>;
}

/** Alerts across every connection (wireframes §15.1). */
export function AlertsPage() {
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const [status, setStatus] = useState("open");
  const list = useApi<AlertEvent[]>(ws ? `/workspaces/${ws}/alerts${query({ status: status || undefined })}` : null);
  usePolling(list.reload, 30_000);
  const [error, setError] = useState<string | null>(null);
  const act = (path: string, body?: unknown) =>
    api.post(path, body).then(list.reload).catch((e: ApiError) => setError(e.message));

  return (
    <>
      <header className="page-header">
        <div><h1>Alerts</h1><p>One alert per episode. It resolves on its own after two healthy checks.</p></div>
        <div className="toolbar"><Link className="btn" to="/lens/incidents">Incidents</Link><Link className="btn" to="/lens/channels">Channels</Link></div>
      </header>
      <div className="segmented" role="group" aria-label="Alert status">
        {[["open", "Open"], ["resolved", "Resolved"], ["", "All"]].map(([v, l]) => (
          <button key={l} type="button" aria-pressed={status === v} onClick={() => setStatus(v ?? "")}>{l}</button>
        ))}
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      <AsyncView state={list} isEmpty={(l) => l.length === 0} empty={status === "open" ? "No open alerts. ✔" : "No alerts."}>
        {(items) => (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Status</th><th scope="col">Severity</th><th scope="col">Alert</th><th scope="col">Connection</th><th scope="col">Opened</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {items.map((a) => (
                  <tr key={a.id}>
                    <td><Status s={a.status} /></td>
                    <td><Sev s={a.severity} /></td>
                    <td><strong>{a.rule_name}</strong><div className="hint">{a.summary}</div>
                      {a.incident_id && <Link to={`/lens/incidents/${a.incident_id}`}>Incident</Link>}
                      {a.runbook_url && <> · <a href={a.runbook_url} target="_blank" rel="noreferrer">Runbook</a></>}</td>
                    <td>{a.connection_id ? <Link to={`/lens/connections/${a.connection_id}`}>{a.connection_name}</Link> : "–"}</td>
                    <td title={a.opened_at}>{timeAgo(a.opened_at)}</td>
                    <td>
                      {can("editor") && a.status !== "resolved" && (
                        <div className="toolbar">
                          {a.status === "firing" && <button className="btn small" type="button" onClick={() => act(`/workspaces/${ws}/alerts/${a.id}/ack`)}>Ack</button>}
                          {a.status !== "muted" && <button className="btn small" type="button" onClick={() => act(`/workspaces/${ws}/alerts/${a.id}/mute`, { until: new Date(Date.now() + 3600_000).toISOString() })}>Mute 1 h</button>}
                          <button className="btn small" type="button" onClick={() => act(`/workspaces/${ws}/alerts/${a.id}/resolve`)}>Resolve</button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AsyncView>
    </>
  );
}

export function IncidentsPage() {
  const { workspace } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const list = useApi<Incident[]>(ws ? `/workspaces/${ws}/incidents` : null);
  return (
    <>
      <header className="page-header"><div><h1>Incidents</h1><p>Critical alerts open an incident automatically; it resolves when they all recover.</p></div></header>
      <AsyncView state={list} isEmpty={(l) => l.length === 0} empty="No incidents.">
        {(items) => (
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Status</th><th scope="col">Severity</th><th scope="col">Incident</th><th scope="col">Opened</th><th scope="col" className="num">Alerts</th></tr></thead>
            <tbody>{items.map((i) => (
              <tr key={i.id}><td><Status s={i.status} /></td><td><Sev s={i.severity} /></td>
                <td><Link to={`/lens/incidents/${i.id}`}>{i.title}</Link>{i.connection_name && <div className="hint">{i.connection_name}</div>}</td>
                <td>{formatDateTime(i.opened_at)}</td><td className="num">{i.alert_count ?? 0}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </AsyncView>
    </>
  );
}

export function IncidentPage() {
  const { incidentId = "" } = useParams();
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const base = `/workspaces/${ws}/incidents/${incidentId}`;
  const inc = useApi<Incident>(ws ? base : null);
  const [note, setNote] = useState("");
  async function addNote(e: FormEvent) {
    e.preventDefault();
    await api.post(`${base}/notes`, { body: note.trim() });
    setNote("");
    inc.reload();
  }
  return (
    <AsyncView state={inc} skeletonRows={4}>
      {(i) => (
        <>
          <header className="page-header">
            <div><p className="breadcrumb"><Link to="/lens/incidents">Incidents</Link> /</p>
              <h1 className="title-row">{i.title} <Status s={i.status} /> <Sev s={i.severity} /></h1>
              <p className="muted">Opened {formatDateTime(i.opened_at)}{i.resolved_at && ` · resolved ${formatDateTime(i.resolved_at)}`}
                {i.runbook_url && <> · <a href={i.runbook_url} target="_blank" rel="noreferrer">Runbook</a></>}</p></div>
            {can("editor") && i.status !== "resolved" && (
              <div className="toolbar">
                {i.status === "open" && <button className="btn" type="button" onClick={() => void api.patch(base, { status: "acknowledged" }).then(inc.reload)}>Acknowledge</button>}
                <button className="btn" type="button" onClick={() => void api.patch(base, { status: "resolved" }).then(inc.reload)}>Resolve</button>
              </div>
            )}
          </header>
          <div className="grid two">
            <Card title="Timeline">
              <ol className="timeline">
                {(i.notes ?? []).map((n) => (
                  <li key={n.id} data-kind={n.kind}><span className="hint">{formatDateTime(n.created_at)}{n.author ? ` · ${n.author}` : ""}</span><p>{n.body}</p></li>
                ))}
              </ol>
              {can("editor") && (
                <form className="stack" onSubmit={addNote}>
                  <label htmlFor="note">Add a note</label>
                  <textarea id="note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
                  <div><button className="btn primary" type="submit" disabled={!note.trim()}>Add note</button></div>
                </form>
              )}
            </Card>
            <Card title="Linked alerts">
              <ul className="plain-list">{(i.alerts ?? []).map((a) => <li key={a.id}><Status s={a.status} /> {a.summary}</li>)}</ul>
            </Card>
          </div>
        </>
      )}
    </AsyncView>
  );
}

/** Notification channels (admin). Webhook signing secrets are shown once, at creation. */
export function ChannelsPage() {
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const list = useApi<Channel[]>(ws ? `/workspaces/${ws}/notification-channels` : null);
  const [form, setForm] = useState({ kind: "webhook", name: "", url: "", emails: "" });
  const [created, setCreated] = useState<Channel | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  async function create(e: FormEvent) {
    e.preventDefault();
    setMessage(null);
    try {
      const body = form.kind === "email"
        ? { kind: "email", name: form.name, emails: form.emails.split(",").map((s) => s.trim()).filter(Boolean) }
        : { kind: form.kind, name: form.name, url: form.url };
      setCreated(await api.post<Channel>(`/workspaces/${ws}/notification-channels`, body));
      setForm({ ...form, name: "", url: "", emails: "" });
      list.reload();
    } catch (err) {
      setMessage((err as ApiError).message);
    }
  }
  async function verify(id: string) {
    const r = await api.post<{ ok: boolean; error?: string }>(`/workspaces/${ws}/notification-channels/${id}/verify`);
    setMessage(r.ok ? "Test notification delivered." : `Delivery failed: ${r.error}`);
    list.reload();
  }
  return (
    <>
      <header className="page-header"><div><h1>Notification channels</h1><p>Where alerts go besides in-app notifications. Secrets are encrypted and never shown again.</p></div></header>
      {message && <p className="notice" role="status">{message}</p>}
      {created?.signing_secret && (
        <div className="banner" data-tone="warning" role="note">
          Signing secret for <strong>{created.name}</strong> (shown once): <code className="mono">{created.signing_secret}</code>.
          Verify <code>X-Lens-Signature: sha256=HMAC(secret, body)</code> on your endpoint.
        </div>
      )}
      <AsyncView state={list} isEmpty={(l) => l.length === 0} empty="No channels yet. Alerts still appear in-app.">
        {(items) => (
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Name</th><th scope="col">Kind</th><th scope="col">Target</th><th scope="col">Verified</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{items.map((c) => (
              <tr key={c.id}><td>{c.name}</td><td>{c.kind}</td><td className="mono">{c.summary}</td><td>{c.is_verified ? "✔" : "–"}</td>
                <td>{can("admin") && <div className="toolbar">
                  <button className="btn small" type="button" onClick={() => void verify(c.id)}>Send test</button>
                  <button className="btn small danger" type="button" onClick={() => void api.delete(`/workspaces/${ws}/notification-channels/${c.id}`).then(list.reload)}>Delete</button>
                </div>}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </AsyncView>
      {can("admin") && (
        <Card title="Add channel">
          <form className="form-grid" onSubmit={create}>
            <div className="field"><label htmlFor="c-kind">Kind</label>
              <select id="c-kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                <option value="webhook">Webhook (signed)</option><option value="slack">Slack incoming webhook</option><option value="email">Email</option>
              </select></div>
            <div className="field"><label htmlFor="c-name">Name</label><input id="c-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></div>
            {form.kind === "email" ? (
              <div className="field span-2"><label htmlFor="c-emails">Recipients (comma-separated)</label><input id="c-emails" value={form.emails} onChange={(e) => setForm({ ...form, emails: e.target.value })} required /></div>
            ) : (
              <div className="field span-2"><label htmlFor="c-url">HTTPS URL</label><input id="c-url" type="url" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} required /></div>
            )}
            <div className="span-2"><button className="btn primary" type="submit">Add channel</button></div>
          </form>
        </Card>
      )}
    </>
  );
}

const RULE_TEMPLATES: Record<AlertRule["rule_type"], { label: string; condition: Record<string, unknown> }> = {
  status: { label: "Database down", condition: { state_in: ["down", "unreachable", "auth_failed", "tls_error"], for_checks: 2 } },
  metric: { label: "Metric threshold", condition: { metric: "latency_ms", op: ">", value: 2000, for_checks: 3 } },
  freshness: { label: "Table not updated", condition: { freshness_table: "public.orders", max_lag_minutes: 120 } },
  row_drop: { label: "Row count dropped", condition: { row_drop_table: "public.orders", drop_pct: 30, window: "1d" } },
  schema_change: { label: "Schema changed", condition: { schema_change_types: ["table_dropped", "column_dropped", "type_changed"] } },
  privilege: { label: "Lens role can write", condition: {} },
};

/** Per-connection alert rules + maintenance windows (wireframes §15.2–15.3). */
export function ConnectionAlertsPage() {
  const { id = "" } = useParams();
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const rules = useApi<AlertRule[]>(ws ? `/workspaces/${ws}/alert-rules?connection_id=${id}` : null);
  const channels = useApi<Channel[]>(ws ? `/workspaces/${ws}/notification-channels` : null);
  const alerts = useApi<AlertEvent[]>(ws ? `/workspaces/${ws}/alerts?connection=${id}` : null);
  const [type, setType] = useState<AlertRule["rule_type"]>("metric");
  const [name, setName] = useState("");
  const [severity, setSeverity] = useState<Severity>("warning");
  const [condition, setCondition] = useState(JSON.stringify(RULE_TEMPLATES.metric.condition, null, 2));
  const [channel, setChannel] = useState("");
  const [hours, setHours] = useState(1);
  const [error, setError] = useState<string | null>(null);

  async function addRule(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post(`/workspaces/${ws}/alert-rules`, { name: name || RULE_TEMPLATES[type].label, connection_id: id, rule_type: type,
        severity, condition: JSON.parse(condition), channels: channel ? [channel] : [] });
      setName("");
      rules.reload();
    } catch (err) {
      setError(err instanceof SyntaxError ? "The condition must be valid JSON." : (err as ApiError).message);
    }
  }

  return (
    <>
      <ConnectionTabs />
      <header className="page-header"><div><h1>Alerts</h1><p>Rules evaluated after every health check and catalog sync.</p></div></header>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="stack">
        <Card title="Recent alerts">
          <AsyncView state={alerts} isEmpty={(l) => l.length === 0} empty="No alerts for this connection.">
            {(items) => <ul className="plain-list">{items.slice(0, 10).map((a) => <li key={a.id}><Status s={a.status} /> <Sev s={a.severity} /> {a.summary} · {timeAgo(a.opened_at)}</li>)}</ul>}
          </AsyncView>
        </Card>
        <Card title="Rules">
          <AsyncView state={rules} isEmpty={(l) => l.length === 0} empty="No rules.">
            {(items) => (
              <div className="table-wrap"><table>
                <thead><tr><th scope="col">Rule</th><th scope="col">Type</th><th scope="col">Severity</th><th scope="col">Condition</th><th scope="col">Enabled</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>{items.map((r) => (
                  <tr key={r.id}><td>{r.name}{r.is_default && <span className="tag">default</span>}{!r.connection_id && <span className="tag">all connections</span>}</td>
                    <td>{r.rule_type}</td><td><Sev s={r.severity} /></td><td className="mono hint">{JSON.stringify(r.condition)}</td>
                    <td><input type="checkbox" aria-label={`Enable ${r.name}`} checked={r.is_enabled} disabled={!can("editor")}
                               onChange={(e) => void api.patch(`/workspaces/${ws}/alert-rules/${r.id}`, { is_enabled: e.target.checked }).then(rules.reload)} /></td>
                    <td>{can("editor") && <div className="toolbar">
                      <button className="btn small" type="button" onClick={() => void api.post<{ results: { ok: boolean }[] }>(`/workspaces/${ws}/alert-rules/${r.id}/test`).then((x) => setError(x.results.length ? `Test sent: ${x.results.filter((y) => y.ok).length}/${x.results.length} delivered` : "This rule has no channels; it notifies in-app only."))}>Test</button>
                      <button className="btn small danger" type="button" onClick={() => void api.delete(`/workspaces/${ws}/alert-rules/${r.id}`).then(rules.reload)}>Delete</button>
                    </div>}</td></tr>
                ))}</tbody>
              </table></div>
            )}
          </AsyncView>
        </Card>
        {can("editor") && (
          <div className="grid two">
            <Card title="Add rule">
              <form className="form-grid" onSubmit={addRule}>
                <div className="field"><label htmlFor="r-type">Type</label>
                  <select id="r-type" value={type} onChange={(e) => { const t = e.target.value as AlertRule["rule_type"]; setType(t); setCondition(JSON.stringify(RULE_TEMPLATES[t].condition, null, 2)); }}>
                    {Object.entries(RULE_TEMPLATES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                  </select></div>
                <div className="field"><label htmlFor="r-sev">Severity</label>
                  <select id="r-sev" value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
                    <option value="info">info</option><option value="warning">warning</option><option value="critical">critical (opens an incident)</option>
                  </select></div>
                <div className="field span-2"><label htmlFor="r-name">Name</label><input id="r-name" placeholder={RULE_TEMPLATES[type].label} value={name} onChange={(e) => setName(e.target.value)} /></div>
                <div className="field span-2"><label htmlFor="r-cond">Condition (JSON)</label><textarea id="r-cond" className="mono" rows={5} value={condition} onChange={(e) => setCondition(e.target.value)} /></div>
                <div className="field span-2"><label htmlFor="r-ch">Notify channel</label>
                  <select id="r-ch" value={channel} onChange={(e) => setChannel(e.target.value)}>
                    <option value="">In-app only</option>{(channels.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name} ({c.kind})</option>)}
                  </select></div>
                <div className="span-2"><button className="btn primary" type="submit">Add rule</button></div>
              </form>
            </Card>
            <Card title="Maintenance window" subtitle="Suppresses alerts for this connection while it runs.">
              <form className="stack" onSubmit={(e) => { e.preventDefault(); const now = new Date(); void api.post(`/workspaces/${ws}/maintenance-windows`, {
                connection_id: id, starts_at: now.toISOString(), ends_at: new Date(now.getTime() + hours * 3600_000).toISOString(), reason: "Planned maintenance" })
                .then(() => setError(`Alerts suppressed for ${hours} h.`)).catch((er: ApiError) => setError(er.message)); }}>
                <div className="field">
                  <label htmlFor="mw-h">Starting now, for</label>
                  <select id="mw-h" value={hours} onChange={(e) => setHours(Number(e.target.value))}>{[1, 2, 4, 8, 24].map((h) => <option key={h} value={h}>{h} hour{h > 1 ? "s" : ""}</option>)}</select>
                </div>
                <div><button className="btn" type="submit">Start maintenance</button></div>
              </form>
            </Card>
          </div>
        )}
      </div>
    </>
  );
}

export function NotificationsPage() {
  const data = useApi<NotificationList>("/notifications");
  return (
    <>
      <header className="page-header"><div><h1>Notifications</h1></div>
        <button className="btn" type="button" onClick={() => void api.post("/notifications/read-all").then(data.reload)}>Mark all read</button></header>
      <AsyncView state={data} isEmpty={(d) => d.items.length === 0} empty="Nothing yet.">
        {(d) => (
          <ul className="plain-list notifications">
            {d.items.map((n) => (
              <li key={n.id} data-unread={!n.read_at}>
                <strong>{n.title}</strong> <span className="hint">{timeAgo(n.created_at)}</span>
                {n.body && <div>{n.body}</div>}
                {n.link && <Link to={n.link}>Open</Link>}
              </li>
            ))}
          </ul>
        )}
      </AsyncView>
    </>
  );
}
