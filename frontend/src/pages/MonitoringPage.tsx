import { useState } from "react";
import Markdown from "react-markdown";

import { api, ApiError } from "../api/client";
import type { Alert, Incident, MonitoringSummary } from "../api/types";
import { useApi } from "../api/useApi";
import { AsyncView } from "../components/AsyncView";
import { Card, KpiCard, ReportSection } from "../components/Card";
import { Icon } from "../components/Icon";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime, formatInt, humanize } from "../lib/format";

export function MonitoringPage() {
  const summary = useApi<MonitoringSummary>("/monitoring/summary");
  const alerts = useApi<Alert[]>("/monitoring/alerts");
  const incidents = useApi<Incident[]>("/incidents");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function evaluate() {
    setBusy(true);
    try {
      const result = await api.post<{ active: number; new: number }>("/monitoring/alerts/evaluate");
      setMessage(`Evaluated alert rules: ${result.active} active, ${result.new} new.`);
      alerts.reload();
      summary.reload();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Evaluation failed");
    } finally {
      setBusy(false);
    }
  }

  async function acknowledge(id: number) {
    try {
      await api.post(`/monitoring/alerts/${id}/acknowledge`);
      alerts.reload();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Could not acknowledge");
    }
  }

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Alerts &amp; incidents</h1>
          <p>Six alert rules watch failed and slow runs, stale data, failing checks, budget and cost spikes. Every incident keeps its postmortem.</p>
        </div>
        <button type="button" className="btn primary" disabled={busy} onClick={evaluate}>
          {busy ? "Evaluating…" : "Evaluate alert rules now"}
        </button>
      </header>
      {message && <div className="notice" role="status" style={{ marginBottom: 16 }}>{message}</div>}

      <div className="report">
        <ReportSection id="health" title="Platform health" description="The state of the platform right now." lineage={<code>ops.pipeline_runs</code>}>
        <AsyncView state={summary}>
          {(s) => (
            <div className="grid kpis">
              <KpiCard label="Failing pipelines" value={formatInt(s.failing_pipelines.length)} hint={s.failing_pipelines.join(", ") || "none"}
                tone={s.failing_pipelines.length ? "critical" : "good"} />
              <KpiCard label="Runs in last 24 h" value={formatInt(Object.values(s.runs_last_24h).reduce((a, b) => a + b, 0))}
                hint={Object.entries(s.runs_last_24h).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ") || "no runs"} />
              <KpiCard label="Long-running runs (30 d)" value={formatInt(s.long_running_runs_30d)} hint="> 2× pipeline median" />
              <KpiCard label="Hours since last ingestion" value={s.hours_since_ingestion?.toFixed(1) ?? "–"}
                hint={s.hours_since_ingestion != null && s.hours_since_ingestion > 26 ? <StatusBadge status="WARNING" /> : "threshold 26 h"} />
              <KpiCard label="Open incidents" value={formatInt(s.open_incidents)} tone={s.open_incidents ? "critical" : undefined} />
            </div>
          )}
        </AsyncView>
        </ReportSection>

        <ReportSection
          id="alerts"
          title="Active alerts"
          description="De-duplicated per rule and subject; each one resolves itself when its condition clears."
          lineage={<code>ops.alerts</code>}
        >
        <Card>
          <AsyncView state={alerts} isEmpty={(a) => a.length === 0} empty="No active alerts.">
            {(list) => (
              <div className="table-wrap">
                <table>
                  <thead><tr><th scope="col">Severity</th><th scope="col">Rule</th><th scope="col">Subject</th><th scope="col">Message</th><th scope="col">Raised</th><th scope="col"><span className="skip-link">Actions</span></th></tr></thead>
                  <tbody>
                    {list.map((a) => (
                      <tr key={a.alert_id}>
                        <td><StatusBadge status={a.severity} /></td>
                        <td>{humanize(a.rule)}</td>
                        <td className="mono">{a.subject}</td>
                        <td>{a.message}</td>
                        <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(a.raised_at)}</td>
                        <td><button type="button" className="btn small" onClick={() => acknowledge(a.alert_id)}>Acknowledge</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </AsyncView>
        </Card>
        </ReportSection>

        <ReportSection
          id="incidents"
          title="Incident history"
          description="Impact, timeline, root cause and resolution for every incident."
          lineage={<code>monitoring/incidents/*.md</code>}
        >
        <Card>
          <AsyncView state={incidents} isEmpty={(i) => i.length === 0} empty="No incidents recorded.">
            {(list) => (
              <div className="stack">
                {list.map((incident) => (
                  <details key={incident.incident_id} className="incident">
                    <summary>
                      <Icon name="arrowRight" size={15} />
                      <span className="toolbar" style={{ display: "inline-flex" }}>
                        <strong>{incident.incident_id}</strong>
                        <StatusBadge status={incident.status} />
                        <span className="badge">{incident.severity}</span>
                        <span>{incident.title}</span>
                      </span>
                    </summary>
                    <dl className="kv" style={{ marginTop: 10 }}>
                      <dt>Pipeline</dt><dd>{incident.pipeline_name ?? "–"}</dd>
                      <dt>Opened</dt><dd>{formatDateTime(incident.opened_at)}</dd>
                      <dt>Resolved</dt><dd>{formatDateTime(incident.resolved_at)}</dd>
                      <dt>Source</dt><dd>{incident.root_cause}</dd>
                      <dt>Runbook</dt><dd><code>{incident.runbook ?? "–"}</code></dd>
                    </dl>
                    <div className="markdown"><Markdown>{incident.resolution ?? ""}</Markdown></div>
                  </details>
                ))}
              </div>
            )}
          </AsyncView>
        </Card>
        </ReportSection>
      </div>
    </>
  );
}
