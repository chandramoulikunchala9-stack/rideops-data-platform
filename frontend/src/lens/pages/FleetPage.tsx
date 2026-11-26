import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { query } from "../../api/client";
import { useApi } from "../../api/useApi";
import { AsyncView } from "../../components/AsyncView";
import { Icon } from "../../components/Icon";
import { Card, KpiCard } from "../../components/Card";
import { useLens } from "../LensContext";
import { Sparkline } from "../components/Sparkline";
import { StateBadge } from "../components/StateBadge";
import { ENGINE_LABEL, ENV_LABEL, STATE_META, STATE_ORDER, formatSince, timeAgo } from "../status";
import type { Connection, ConnState, FleetOverview } from "../types";

const POLL_MS = 30_000;

/** Re-fetch every `ms` while the tab is visible (status pages stay live without a refresh button). */
// eslint-disable-next-line react-refresh/only-export-components
export function usePolling(reload: () => void, ms: number) {
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, ms);
    return () => window.clearInterval(id);
  }, [reload, ms]);
}

function Onboarding({ connections }: { connections: Connection[] }) {
  const { me, can } = useLens();
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem("lens-onboarding-dismissed") === "1";
    } catch {
      return false;
    }
  });
  const demo = connections.find((c) => c.is_demo);
  const own = connections.some((c) => !c.is_demo);
  // The demo step only exists when this server has a demo warehouse; it is done once opened.
  const demoSeen = (() => {
    try {
      return localStorage.getItem("lens-demo-explored") === "1";
    } catch {
      return false;
    }
  })();
  const steps = [
    { done: true, label: "Create account" },
    { done: Boolean(me?.user.email_verified), label: "Verify your email (needed before adding connections)" },
    ...(demo ? [{ done: demoSeen, label: "Explore the demo warehouse", to: `/lens/connections/${demo.id}` }] : []),
    { done: own, label: "Connect your own database", to: can("editor") ? "/lens/connections/new" : undefined },
  ];
  const complete = steps.filter((s) => s.done).length;
  if (dismissed || complete === steps.length) return null;
  return (
    <Card title="Getting started" subtitle={`${complete} / ${steps.length} complete`}
          actions={<button className="btn small" type="button" onClick={() => {
            setDismissed(true);
            try { localStorage.setItem("lens-onboarding-dismissed", "1"); } catch { /* ignore */ }
          }}>Dismiss</button>}>
      <ul className="checklist">
        {steps.map((s) => (
          <li key={s.label} data-done={s.done}>
            <span className="checklist-mark" aria-hidden="true">{s.done ? <Icon name="check" size={14} /> : null}</span>
            <span className="sr-only">{s.done ? "Done: " : "To do: "}</span>{s.label}
            {!s.done && s.to && <> · <Link to={s.to}>Start</Link></>}
            {s.done && s.to && <> · <Link to={s.to}>Open</Link></>}
          </li>
        ))}
      </ul>
      <p className="notice"><span>Tip: use a <strong>read-only</strong> database user. The wizard shows the exact SQL to create one.</span></p>
    </Card>
  );
}

export function FleetPage() {
  const { workspace, can } = useLens();
  const navigate = useNavigate();
  const ws = workspace?.workspace_id;
  const [state, setState] = useState("");
  const [env, setEnv] = useState("");
  const [search, setSearch] = useState("");
  const list = useApi<Connection[]>(ws ? `/workspaces/${ws}/connections${query({ state, env })}` : null);
  const overview = useApi<FleetOverview>(ws ? `/workspaces/${ws}/status/overview` : null);
  usePolling(list.reload, POLL_MS);
  usePolling(overview.reload, POLL_MS);

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return [...(list.data ?? [])]
      .filter((c) => !term || c.name.toLowerCase().includes(term) || c.tags.some((t) => t.includes(term)))
      .sort((a, b) => STATE_ORDER.indexOf(a.current_state) - STATE_ORDER.indexOf(b.current_state)
        || (a.health_score ?? 0) - (b.health_score ?? 0) || a.name.localeCompare(b.name));
  }, [list.data, search]);

  const counts = overview.data?.by_state ?? {};
  const down = (counts.down ?? 0) + (counts.unreachable ?? 0) + (counts.auth_failed ?? 0) + (counts.tls_error ?? 0);

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Databases</h1>
          <p>Every connected database, worst first. Checks refresh every 30 seconds.</p>
        </div>
        {can("editor") && <Link className="btn primary" to="/lens/connections/new">+ Add connection</Link>}
      </header>
      <div className="stack">
        <div className="grid four">
          <KpiCard label="Healthy" value={counts.healthy ?? 0} tone={counts.healthy ? "good" : undefined} />
          <KpiCard label="Degraded" value={counts.degraded ?? 0} tone={counts.degraded ? "warning" : undefined} />
          <KpiCard label="Down or failing" value={down} tone={down ? "critical" : undefined} />
          <KpiCard label="Paused / unknown" value={(counts.paused ?? 0) + (counts.unknown ?? 0)} />
        </div>
        {list.data && <Onboarding connections={list.data} />}
        <Card title="Connections">
          <div className="filters" role="group" aria-label="Filters">
            <div className="field">
              <label htmlFor="f-state">State</label>
              <select id="f-state" value={state} onChange={(e) => setState(e.target.value)}>
                <option value="">All states</option>
                {STATE_ORDER.map((s) => <option key={s} value={s}>{STATE_META[s].label}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="f-env">Environment</label>
              <select id="f-env" value={env} onChange={(e) => setEnv(e.target.value)}>
                <option value="">All environments</option>
                {Object.entries(ENV_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="f-search">Name or tag</label>
              <input id="f-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
          </div>
          <AsyncView state={list} isEmpty={(d) => d.length === 0} empty={
            <div>
              <p>No databases connected yet.</p>
              {can("editor") && <Link className="btn primary" to="/lens/connections/new">Add your first connection</Link>}
            </div>
          }>
            {() => (
              <div className="table-wrap">
                <table className="clickable">
                  <thead>
                    <tr>
                      <th scope="col">Status</th><th scope="col">In state for</th><th scope="col">Name</th><th scope="col">Engine</th>
                      <th scope="col">Env</th><th scope="col" className="num">Score</th>
                      <th scope="col">Latency</th><th scope="col">Last check</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((c) => (
                      <tr key={c.id} onClick={() => navigate(`/lens/connections/${c.id}`)}>
                        <td><StateBadge state={c.current_state as ConnState} /></td>
                        <td className="nowrap" title={c.last_state_change_at ?? undefined}>{formatSince(c.last_state_change_at) ?? "–"}</td>
                        <td>
                          <Link to={`/lens/connections/${c.id}`} onClick={(e) => e.stopPropagation()}>{c.name}</Link>
                          {c.tags.length > 0 && <div className="tags">{c.tags.map((t) => <span key={t} className="tag">{t}</span>)}</div>}
                        </td>
                        <td>{c.server_version ?? ENGINE_LABEL[c.engine] ?? c.engine}</td>
                        <td><span className="env-chip" data-env={c.environment}>{ENV_LABEL[c.environment] ?? c.environment}</span></td>
                        <td className="num">{c.health_score ?? "–"}</td>
                        <td>
                          <span className="latency-cell">
                            <Sparkline values={c.sparkline} label={`${c.name} latency trend`} />
                            <span className="num">{c.sparkline.at(-1) != null ? `${c.sparkline.at(-1)} ms` : "–"}</span>
                          </span>
                        </td>
                        <td title={c.last_checked_at ?? undefined}>{c.is_paused ? "paused" : timeAgo(c.last_checked_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </AsyncView>
        </Card>
        {overview.data && overview.data.egress_ips.length > 0 && (
          <p className="table-note">Allowlist Lens on your database firewall: <code>{overview.data.egress_ips.join(", ")}</code></p>
        )}
      </div>
    </>
  );
}
