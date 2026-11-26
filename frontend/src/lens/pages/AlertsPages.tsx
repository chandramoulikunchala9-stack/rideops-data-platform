import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError, query } from "../../api/client";
import { useApi } from "../../api/useApi";
import { AsyncView } from "../../components/AsyncView";
import { Card } from "../../components/Card";
import { Icon, type IconName } from "../../components/Icon";
import { formatDateTime, formatInt } from "../../lib/format";
import { useLens } from "../LensContext";
import { ConfirmButton } from "../components/ConfirmButton";
import { ConnectionTabs } from "../components/ConnectionTabs";
import { timeAgo } from "../status";
import type { AlertEvent, AlertRule, Catalog, Channel, Incident, NotificationList, Severity } from "../types";
import { usePolling } from "./FleetPage";

type Tone = "critical" | "warning" | "good" | "neutral";

// Severity and status always travel as icon + label + tint (never colour alone).
const SEV_META: Record<Severity, { tone: Tone; icon: IconName; label: string }> = {
  critical: { tone: "critical", icon: "x", label: "Critical" },
  warning: { tone: "warning", icon: "warning", label: "Warning" },
  info: { tone: "neutral", icon: "activity", label: "Info" },
};
const STATUS_META: Record<string, { tone: Tone; icon: IconName; label: string }> = {
  firing: { tone: "critical", icon: "alerts", label: "Firing" },
  open: { tone: "critical", icon: "alerts", label: "Open" },
  acknowledged: { tone: "warning", icon: "clock", label: "Acknowledged" },
  muted: { tone: "neutral", icon: "ban", label: "Muted" },
  resolved: { tone: "good", icon: "check", label: "Resolved" },
};

function Sev({ s }: { s: Severity }) {
  const meta = SEV_META[s] ?? SEV_META.info;
  return <span className="badge" data-tone={meta.tone}><Icon name={meta.icon} size={13} />{meta.label}</span>;
}

function Status({ s }: { s: string }) {
  const meta = STATUS_META[s] ?? { tone: "neutral" as Tone, icon: "clock" as IconName, label: s };
  return <span className="badge" data-tone={meta.tone}><Icon name={meta.icon} size={13} />{meta.label}</span>;
}

type Notice = { tone: "success" | "error"; text: string } | null;

function NoticeLine({ notice }: { notice: Notice }) {
  if (!notice) return null;
  return (
    <p className={`notice ${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>{notice.text}</p>
  );
}

/** Alerts across every connection. */
export function AlertsPage() {
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const [status, setStatus] = useState("open");
  const list = useApi<AlertEvent[]>(ws ? `/workspaces/${ws}/alerts${query({ status: status || undefined })}` : null);
  usePolling(list.reload, 30_000);
  const [notice, setNotice] = useState<Notice>(null);
  const act = (path: string, done: string, body?: unknown) =>
    api.post(path, body)
      .then(() => { setNotice({ tone: "success", text: done }); list.reload(); })
      .catch((e: ApiError) => setNotice({ tone: "error", text: e.message }));

  return (
    <>
      <header className="page-header">
        <div><h1>Alerts</h1><p>One alert per episode. It resolves on its own after two healthy checks.</p></div>
        <div className="toolbar"><Link className="btn" to="/lens/channels">Notification channels</Link></div>
      </header>
      <div className="segmented" role="group" aria-label="Alert status">
        {[["open", "Open"], ["resolved", "Resolved"], ["", "All"]].map(([v, l]) => (
          <button key={l} type="button" aria-pressed={status === v} onClick={() => setStatus(v ?? "")}>{l}</button>
        ))}
      </div>
      <NoticeLine notice={notice} />
      <AsyncView state={list} isEmpty={(l) => l.length === 0}
                 empty={status === "open" ? "No open alerts: every rule is quiet." : "No alerts in this view."}>
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
                    <td title={formatDateTime(a.opened_at)}>{timeAgo(a.opened_at)}</td>
                    <td>
                      {can("editor") && a.status !== "resolved" && (
                        <div className="toolbar">
                          {a.status === "firing" && <button className="btn small" type="button" onClick={() => act(`/workspaces/${ws}/alerts/${a.id}/ack`, `Acknowledged: ${a.rule_name}.`)}>Acknowledge</button>}
                          {a.status !== "muted" && <button className="btn small" type="button" onClick={() => act(`/workspaces/${ws}/alerts/${a.id}/mute`, `Muted for 1 hour: ${a.rule_name}.`, { until: new Date(Date.now() + 3600_000).toISOString() })}>Mute 1 h</button>}
                          <button className="btn small" type="button" onClick={() => act(`/workspaces/${ws}/alerts/${a.id}/resolve`, `Resolved: ${a.rule_name}.`)}>Resolve</button>
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
      <AsyncView state={list} isEmpty={(l) => l.length === 0} empty="No incidents. A critical alert opens one automatically.">
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
  const [notice, setNotice] = useState<Notice>(null);
  async function addNote(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post(`${base}/notes`, { body: note.trim() });
      setNote("");
      inc.reload();
    } catch (err) {
      setNotice({ tone: "error", text: (err as ApiError).message });
    }
  }
  const setStatus = (status: string, done: string) =>
    void api.patch(base, { status }).then(() => { setNotice({ tone: "success", text: done }); inc.reload(); })
      .catch((err: ApiError) => setNotice({ tone: "error", text: err.message }));
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
                {i.status === "open" && <button className="btn" type="button" onClick={() => setStatus("acknowledged", "Incident acknowledged.")}>Acknowledge</button>}
                <button className="btn" type="button" onClick={() => setStatus("resolved", "Incident resolved.")}>Resolve</button>
              </div>
            )}
          </header>
          <NoticeLine notice={notice} />
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

const CHANNEL_KIND: Record<string, string> = { webhook: "Webhook", slack: "Slack", email: "Email", inapp: "In-app" };

/** Notification channels (admin). Webhook signing secrets are shown once, at creation. */
export function ChannelsPage() {
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const list = useApi<Channel[]>(ws ? `/workspaces/${ws}/notification-channels` : null);
  const [form, setForm] = useState({ kind: "webhook", name: "", url: "", emails: "" });
  const [created, setCreated] = useState<Channel | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  async function create(e: FormEvent) {
    e.preventDefault();
    setNotice(null);
    try {
      const body = form.kind === "email"
        ? { kind: "email", name: form.name, emails: form.emails.split(",").map((s) => s.trim()).filter(Boolean) }
        : { kind: form.kind, name: form.name, url: form.url };
      setCreated(await api.post<Channel>(`/workspaces/${ws}/notification-channels`, body));
      setForm({ ...form, name: "", url: "", emails: "" });
      list.reload();
    } catch (err) {
      setNotice({ tone: "error", text: (err as ApiError).message });
    }
  }
  async function verify(c: Channel) {
    try {
      const r = await api.post<{ ok: boolean; error?: string }>(`/workspaces/${ws}/notification-channels/${c.id}/verify`);
      setNotice(r.ok ? { tone: "success", text: `Test notification delivered to ${c.name}.` }
                     : { tone: "error", text: `Delivery to ${c.name} failed: ${r.error ?? "no reason given"}.` });
      list.reload();
    } catch (err) {
      setNotice({ tone: "error", text: (err as ApiError).message });
    }
  }
  function remove(c: Channel) {
    void api.delete(`/workspaces/${ws}/notification-channels/${c.id}`)
      .then(() => { setNotice({ tone: "success", text: `Deleted ${c.name}. Rules that used it now notify in-app only.` }); list.reload(); })
      .catch((err: ApiError) => setNotice({ tone: "error", text: err.message }));
  }
  return (
    <>
      <header className="page-header">
        <div><p className="breadcrumb"><Link to="/lens/alerts">Alerts</Link> /</p>
          <h1>Notification channels</h1><p>Where alerts go besides in-app notifications. Secrets are encrypted and never shown again.</p></div>
      </header>
      <NoticeLine notice={notice} />
      {created?.signing_secret && (
        <div className="banner" data-tone="warning" role="note">
          Signing secret for <strong>{created.name}</strong> (shown once): <code className="mono">{created.signing_secret}</code>.
          Verify <code>X-Lens-Signature: sha256=HMAC(secret, body)</code> on your endpoint.
        </div>
      )}
      <AsyncView state={list} isEmpty={(l) => l.length === 0} empty="No channels yet. Alerts still appear in-app.">
        {(items) => (
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Name</th><th scope="col">Kind</th><th scope="col">Target</th><th scope="col">Delivery</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{items.map((c) => (
              <tr key={c.id}><td>{c.name}</td><td>{CHANNEL_KIND[c.kind] ?? c.kind}</td><td className="mono">{c.summary}</td>
                <td>{c.is_verified
                  ? <span className="badge" data-tone="good"><Icon name="check" size={13} />Verified</span>
                  : <span className="badge"><Icon name="clock" size={13} />Not tested yet</span>}</td>
                <td>{can("admin") && <div className="toolbar">
                  <button className="btn small" type="button" onClick={() => void verify(c)}>Send test</button>
                  <ConfirmButton small label="Delete" confirmLabel={`Delete ${c.name}`} onConfirm={() => remove(c)} />
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

// ---- alert rules: built from fields, read as a sentence ------------------------

type RuleType = AlertRule["rule_type"];

const RULE_TYPES: { type: RuleType; label: string }[] = [
  { type: "status", label: "Database down or unreachable" },
  { type: "metric", label: "A health figure crosses a threshold" },
  { type: "freshness", label: "A table stops updating" },
  { type: "row_drop", label: "A table's row count drops" },
  { type: "schema_change", label: "The schema changes" },
  { type: "privilege", label: "Lens's role can write" },
];

// Metrics the evaluator reads from each status snapshot (backend lens/alerts/conditions.py).
const METRICS: { key: string; label: string; unit: string }[] = [
  { key: "latency_ms", label: "latency", unit: "ms" },
  { key: "connections.used_pct", label: "sessions in use", unit: "% of max" },
  { key: "replication.lag_s", label: "replication lag", unit: "s" },
  { key: "queries.long_running", label: "long-running queries", unit: "queries" },
  { key: "locks.blocked", label: "blocked sessions", unit: "sessions" },
  { key: "cache.hit_ratio", label: "cache hit ratio", unit: "(0–1)" },
  { key: "pg.xid_age_pct", label: "transaction ID age", unit: "%" },
];
const OPS: { op: string; label: string }[] = [
  { op: ">", label: "above" }, { op: ">=", label: "at or above" }, { op: "<", label: "below" }, { op: "<=", label: "at or below" },
];
const CHANGE_TYPES: { key: string; label: string }[] = [
  { key: "table_dropped", label: "a table is dropped" },
  { key: "column_dropped", label: "a column is dropped" },
  { key: "type_changed", label: "a column changes type" },
];
const WINDOWS: Record<string, string> = { "1h": "1 hour", "1d": "1 day", "7d": "7 days" };

interface RuleDraft {
  type: RuleType;
  metric: string;
  op: string;
  value: string;
  forChecks: string;
  table: string;
  lagMinutes: string;
  dropPct: string;
  window: string;
  changes: string[];
}

const EMPTY_DRAFT: RuleDraft = {
  type: "metric", metric: "latency_ms", op: ">", value: "2000", forChecks: "3", table: "",
  lagMinutes: "120", dropPct: "30", window: "1d", changes: CHANGE_TYPES.map((c) => c.key),
};

function plural(n: number, one: string, many = `${one}s`) {
  return `${formatInt(n)} ${n === 1 ? one : many}`;
}

function draftCondition(d: RuleDraft): Record<string, unknown> {
  switch (d.type) {
    case "status": return { for_checks: Number(d.forChecks) || 2 };
    case "metric": return { metric: d.metric, op: d.op, value: Number(d.value), for_checks: Number(d.forChecks) || 1 };
    case "freshness": return { freshness_table: d.table, max_lag_minutes: Number(d.lagMinutes) };
    case "row_drop": return { row_drop_table: d.table, drop_pct: Number(d.dropPct), window: d.window };
    case "schema_change": return { schema_change_types: d.changes };
    case "privilege": return {};
  }
}

/** The rule as a sentence: "Alert when latency is above 2,000 ms for 3 checks in a row." */
function describeRule(type: RuleType, c: Record<string, unknown>): string {
  const n = (v: unknown, fallback: number) => (typeof v === "number" ? v : Number(v ?? fallback));
  switch (type) {
    case "status":
      return `Alert when the database is down or unreachable for ${plural(n(c.for_checks, 2), "check")} in a row.`;
    case "metric": {
      const m = METRICS.find((x) => x.key === c.metric);
      const op = OPS.find((x) => x.op === c.op)?.label ?? String(c.op ?? ">");
      const checks = n(c.for_checks, 1);
      return `Alert when ${m?.label ?? String(c.metric)} is ${op} ${formatInt(n(c.value, 0))} ${m?.unit ?? ""}`.trimEnd()
        + (checks > 1 ? ` for ${plural(checks, "check")} in a row.` : " on a check.");
    }
    case "freshness":
      return `Alert when ${String(c.freshness_table ?? "a table")} has no new rows for ${plural(n(c.max_lag_minutes, 120), "minute")}.`;
    case "row_drop":
      return `Alert when ${String(c.row_drop_table ?? "a table")} loses more than ${n(c.drop_pct, 30)}% of its rows within ${WINDOWS[String(c.window ?? "1d")] ?? String(c.window)}.`;
    case "schema_change": {
      const types = (c.schema_change_types as string[] | undefined) ?? CHANGE_TYPES.map((x) => x.key);
      const words = types.map((t) => CHANGE_TYPES.find((x) => x.key === t)?.label ?? t.replace(/_/g, " "));
      return `Alert when ${words.length > 1 ? `${words.slice(0, -1).join(", ")} or ${words.at(-1)}` : words[0] ?? "the schema changes"}.`;
    }
    case "privilege":
      return "Alert when the role Lens connects with gains write access.";
  }
}

function draftProblem(d: RuleDraft): string | null {
  if (d.type === "metric" && (d.value.trim() === "" || Number.isNaN(Number(d.value)))) return "Enter a number for the threshold.";
  if ((d.type === "freshness" || d.type === "row_drop") && !d.table) return "Choose the table to watch.";
  if (d.type === "schema_change" && d.changes.length === 0) return "Pick at least one kind of change.";
  return null;
}

/** Per-connection alert rules + maintenance windows. */
export function ConnectionAlertsPage() {
  const { id = "" } = useParams();
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const rules = useApi<AlertRule[]>(ws ? `/workspaces/${ws}/alert-rules?connection_id=${id}` : null);
  const channels = useApi<Channel[]>(ws ? `/workspaces/${ws}/notification-channels` : null);
  const alerts = useApi<AlertEvent[]>(ws ? `/workspaces/${ws}/alerts?connection=${id}` : null);
  const catalog = useApi<Catalog>(ws && can("editor") ? `/workspaces/${ws}/connections/${id}/catalog` : null);
  const [draft, setDraft] = useState<RuleDraft>(EMPTY_DRAFT);
  const [name, setName] = useState("");
  const [severity, setSeverity] = useState<Severity>("warning");
  const [channel, setChannel] = useState("");
  const [hours, setHours] = useState(1);
  const [notice, setNotice] = useState<Notice>(null);
  const tables = (catalog.data?.tables ?? []).map((t) => `${t.schema_name}.${t.table_name}`);
  const set = (patch: Partial<RuleDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const problem = draftProblem(draft);
  const typeLabel = RULE_TYPES.find((t) => t.type === draft.type)?.label ?? draft.type;

  async function addRule(e: FormEvent) {
    e.preventDefault();
    if (problem) return setNotice({ tone: "error", text: problem });
    setNotice(null);
    try {
      const ruleName = name.trim() || typeLabel;
      await api.post(`/workspaces/${ws}/alert-rules`, { name: ruleName, connection_id: id, rule_type: draft.type,
        severity, condition: draftCondition(draft), channels: channel ? [channel] : [] });
      setName("");
      setNotice({ tone: "success", text: `Rule added: ${ruleName}.` });
      rules.reload();
    } catch (err) {
      setNotice({ tone: "error", text: (err as ApiError).message });
    }
  }

  async function testRule(r: AlertRule) {
    try {
      const x = await api.post<{ results: { ok: boolean }[] }>(`/workspaces/${ws}/alert-rules/${r.id}/test`);
      const ok = x.results.filter((y) => y.ok).length;
      setNotice(x.results.length === 0
        ? { tone: "success", text: `${r.name} has no channels, so it notifies in-app only.` }
        : ok === x.results.length ? { tone: "success", text: `Test sent for ${r.name}: delivered to all ${x.results.length} channels.` }
        : { tone: "error", text: `Test for ${r.name}: delivered to ${ok} of ${x.results.length} channels. Check the channel settings.` });
    } catch (err) {
      setNotice({ tone: "error", text: (err as ApiError).message });
    }
  }

  function removeRule(r: AlertRule) {
    void api.delete(`/workspaces/${ws}/alert-rules/${r.id}`)
      .then(() => { setNotice({ tone: "success", text: `Deleted rule: ${r.name}.` }); rules.reload(); })
      .catch((err: ApiError) => setNotice({ tone: "error", text: err.message }));
  }

  function startMaintenance(e: FormEvent) {
    e.preventDefault();
    const now = new Date();
    void api.post(`/workspaces/${ws}/maintenance-windows`, {
      connection_id: id, starts_at: now.toISOString(), ends_at: new Date(now.getTime() + hours * 3600_000).toISOString(), reason: "Planned maintenance" })
      .then(() => setNotice({ tone: "success", text: `Maintenance started: alerts for this connection are suppressed for ${plural(hours, "hour")}.` }))
      .catch((er: ApiError) => setNotice({ tone: "error", text: er.message }));
  }

  const tableField = (label: string) => (
    <div className="field span-2"><label htmlFor="r-table">{label}</label>
      {tables.length > 0 ? (
        <select id="r-table" value={draft.table} onChange={(e) => set({ table: e.target.value })}>
          <option value="">Choose a table…</option>
          {tables.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      ) : (
        <input id="r-table" className="mono" placeholder="schema.table" value={draft.table} onChange={(e) => set({ table: e.target.value.trim() })} />
      )}
    </div>
  );

  return (
    <>
      <ConnectionTabs />
      <header className="page-header"><div><h1>Alerts</h1><p>Rules run after every health check and catalog sync.</p></div></header>
      <NoticeLine notice={notice} />
      <div className="stack">
        <Card title="Recent alerts">
          <AsyncView state={alerts} isEmpty={(l) => l.length === 0} empty="No alerts for this connection yet.">
            {(items) => <ul className="plain-list">{items.slice(0, 10).map((a) => <li key={a.id}><Status s={a.status} /> <Sev s={a.severity} /> {a.summary} · {timeAgo(a.opened_at)}</li>)}</ul>}
          </AsyncView>
        </Card>
        <Card title="Rules">
          <AsyncView state={rules} isEmpty={(l) => l.length === 0} empty="No rules yet. Add one below.">
            {(items) => (
              <div className="table-wrap"><table>
                <thead><tr><th scope="col">Rule</th><th scope="col">When it fires</th><th scope="col">Severity</th><th scope="col">On</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>{items.map((r) => (
                  <tr key={r.id}><td>{r.name}{r.is_default && <span className="tag">default</span>}{!r.connection_id && <span className="tag">all connections</span>}</td>
                    <td className="rule-sentence">{describeRule(r.rule_type, r.condition)}</td><td><Sev s={r.severity} /></td>
                    <td><input type="checkbox" aria-label={`Enable ${r.name}`} checked={r.is_enabled} disabled={!can("editor")}
                               onChange={(e) => void api.patch(`/workspaces/${ws}/alert-rules/${r.id}`, { is_enabled: e.target.checked }).then(rules.reload)} /></td>
                    <td>{can("editor") && <div className="toolbar">
                      <button className="btn small" type="button" onClick={() => void testRule(r)}>Send test</button>
                      <ConfirmButton small label="Delete" confirmLabel={`Delete ${r.name}`} onConfirm={() => removeRule(r)} />
                    </div>}</td></tr>
                ))}</tbody>
              </table></div>
            )}
          </AsyncView>
        </Card>
        {can("editor") && (
          <div className="grid two">
            <Card title="Add rule">
              <form className="form-grid" onSubmit={addRule} noValidate>
                <div className="field span-2"><label htmlFor="r-type">Alert when</label>
                  <select id="r-type" value={draft.type} onChange={(e) => set({ type: e.target.value as RuleType, table: "" })}>
                    {RULE_TYPES.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
                  </select></div>

                {draft.type === "metric" && (<>
                  <div className="field"><label htmlFor="r-metric">Figure</label>
                    <select id="r-metric" value={draft.metric} onChange={(e) => set({ metric: e.target.value })}>
                      {METRICS.map((m) => <option key={m.key} value={m.key}>{m.label[0]!.toUpperCase() + m.label.slice(1)}</option>)}
                    </select></div>
                  <div className="field"><label htmlFor="r-op">Is</label>
                    <select id="r-op" value={draft.op} onChange={(e) => set({ op: e.target.value })}>
                      {OPS.map((o) => <option key={o.op} value={o.op}>{o.label}</option>)}
                    </select></div>
                  <div className="field"><label htmlFor="r-value">Threshold ({METRICS.find((m) => m.key === draft.metric)?.unit})</label>
                    <input id="r-value" inputMode="decimal" value={draft.value} onChange={(e) => set({ value: e.target.value })} /></div>
                </>)}
                {(draft.type === "metric" || draft.type === "status") && (
                  <div className="field"><label htmlFor="r-checks">For this many checks in a row</label>
                    <select id="r-checks" value={draft.forChecks} onChange={(e) => set({ forChecks: e.target.value })}>
                      {["1", "2", "3", "5", "10"].map((v) => <option key={v} value={v}>{v}</option>)}
                    </select></div>
                )}
                {draft.type === "freshness" && (<>
                  {tableField("Table")}
                  <div className="field"><label htmlFor="r-lag">No new rows for (minutes)</label>
                    <input id="r-lag" inputMode="numeric" value={draft.lagMinutes} onChange={(e) => set({ lagMinutes: e.target.value })} /></div>
                </>)}
                {draft.type === "row_drop" && (<>
                  {tableField("Table")}
                  <div className="field"><label htmlFor="r-drop">Drops by more than (%)</label>
                    <input id="r-drop" inputMode="numeric" value={draft.dropPct} onChange={(e) => set({ dropPct: e.target.value })} /></div>
                  <div className="field"><label htmlFor="r-window">Within</label>
                    <select id="r-window" value={draft.window} onChange={(e) => set({ window: e.target.value })}>
                      {Object.entries(WINDOWS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select></div>
                </>)}
                {draft.type === "schema_change" && (
                  <fieldset className="field span-2 checks"><legend>Changes to watch</legend>
                    {CHANGE_TYPES.map((c) => (
                      <label key={c.key} className="check">
                        <input type="checkbox" checked={draft.changes.includes(c.key)}
                               onChange={(e) => set({ changes: e.target.checked ? [...draft.changes, c.key] : draft.changes.filter((x) => x !== c.key) })} />
                        {c.label[0]!.toUpperCase() + c.label.slice(1)}
                      </label>
                    ))}
                  </fieldset>
                )}

                <p className="rule-preview span-2" aria-live="polite">
                  {problem ?? describeRule(draft.type, draftCondition(draft))}
                </p>
                <div className="field"><label htmlFor="r-sev">Severity</label>
                  <select id="r-sev" value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
                    <option value="info">Info</option><option value="warning">Warning</option><option value="critical">Critical (opens an incident)</option>
                  </select></div>
                <div className="field"><label htmlFor="r-ch">Notify</label>
                  <select id="r-ch" value={channel} onChange={(e) => setChannel(e.target.value)}>
                    <option value="">In-app only</option>{(channels.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name} ({CHANNEL_KIND[c.kind] ?? c.kind})</option>)}
                  </select></div>
                <div className="field span-2"><label htmlFor="r-name">Name <span className="optional">(optional)</span></label>
                  <input id="r-name" placeholder={typeLabel} value={name} onChange={(e) => setName(e.target.value)} /></div>
                <div className="span-2"><button className="btn primary" type="submit" disabled={Boolean(problem)}>Add rule</button></div>
              </form>
            </Card>
            <Card title="Maintenance window" subtitle="Suppresses alerts for this connection while it runs.">
              <form className="stack" onSubmit={startMaintenance}>
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
  const [notice, setNotice] = useState<Notice>(null);
  const unread = data.data?.unread ?? 0;
  return (
    <>
      <header className="page-header">
        <div><h1>Notifications</h1><p>Alerts and workspace events, newest first.</p></div>
        <button className="btn" type="button" disabled={unread === 0}
                onClick={() => void api.post("/notifications/read-all").then(() => { setNotice({ tone: "success", text: "All notifications marked as read." }); data.reload(); })
                  .catch((err: ApiError) => setNotice({ tone: "error", text: err.message }))}>
          Mark all read</button>
      </header>
      <NoticeLine notice={notice} />
      <AsyncView state={data} isEmpty={(d) => d.items.length === 0} empty="No notifications yet. Alerts and invitations appear here.">
        {(d) => (
          <ul className="plain-list notifications">
            {d.items.map((n) => (
              <li key={n.id} data-unread={!n.read_at}>
                {!n.read_at && <span className="sr-only">Unread: </span>}
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
