import { useState } from "react";
import { useParams } from "react-router-dom";

import { api, ApiError } from "../../api/client";
import { useApi } from "../../api/useApi";
import { AsyncView } from "../../components/AsyncView";
import { Icon, type IconName } from "../../components/Icon";
import { formatInt } from "../../lib/format";
import { useLens } from "../LensContext";
import { ConfirmButton } from "../components/ConfirmButton";
import { ConnectionTabs } from "../components/ConnectionTabs";
import { formatBytes, timeAgo } from "../status";
import type { Insight, Severity } from "../types";

// Findings from the catalog and statistics, grouped by kind: "10 tables are stale" reads as one
// decision, not ten cards. Each group lists the affected objects with the evidence in words.

const SEV_ORDER: Severity[] = ["critical", "warning", "info"];
const SEV_META: Record<Severity, { tone: string; icon: IconName; label: string }> = {
  critical: { tone: "critical", icon: "x", label: "Critical" },
  warning: { tone: "warning", icon: "warning", label: "Warning" },
  info: { tone: "neutral", icon: "activity", label: "Info" },
};

// Group headings per insight code (backend lens/insights/rules.py). n is the number of findings.
const GROUP_TITLE: Record<string, (n: number) => string> = {
  TABLE_STALE: (n) => (n === 1 ? "1 table has stopped updating" : `${n} tables have stopped updating`),
  NO_PRIMARY_KEY: (n) => (n === 1 ? "1 table has no primary key" : `${n} tables have no primary key`),
  UNUSED_INDEX: (n) => (n === 1 ? "1 index is never used" : `${n} indexes are never used`),
  DUPLICATE_INDEX: (n) => (n === 1 ? "1 index duplicates another" : `${n} indexes duplicate others`),
  FK_WITHOUT_INDEX: (n) => (n === 1 ? "1 foreign key has no index" : `${n} foreign keys have no index`),
  HIGH_DEAD_TUPLES: (n) => (n === 1 ? "1 table has many dead rows" : `${n} tables have many dead rows`),
  SEQ_SCAN_HEAVY: (n) => (n === 1 ? "1 table is read mostly by full scans" : `${n} tables are read mostly by full scans`),
  TABLE_GROWING_FAST: (n) => (n === 1 ? "1 table grew fast this week" : `${n} tables grew fast this week`),
};

function num(v: unknown): number | null {
  return typeof v === "number" ? v : v != null && !Number.isNaN(Number(v)) ? Number(v) : null;
}

/** The evidence in words, per code; anything unknown falls back to "key: value" pairs. */
function describeDetail(i: Insight): string {
  const d = i.detail;
  switch (i.code) {
    case "TABLE_STALE": {
      const lag = num(d.lag_days);
      return `${lag != null ? `No new rows for ${formatInt(lag)} ${lag === 1 ? "day" : "days"}` : "No recent rows"}${d.column ? ` (by ${String(d.column)})` : ""}`;
    }
    case "UNUSED_INDEX": return `${formatBytes(num(d.bytes))} on disk${d.table ? `, on ${String(d.table)}` : ""}`;
    case "DUPLICATE_INDEX": return Array.isArray(d.columns) ? `Same columns: ${d.columns.join(", ")}` : "";
    case "FK_WITHOUT_INDEX": return d.references ? `References ${String(d.references)}` : d.ref_table ? `References ${String(d.ref_table)}` : "";
    case "HIGH_DEAD_TUPLES": return `${formatInt(num(d.dead_rows))} dead rows next to ${formatInt(num(d.live_rows))} live`;
    case "SEQ_SCAN_HEAVY": return `${formatInt(num(d.seq_scans))} full scans, ${formatInt(num(d.idx_scans))} index scans`;
    case "TABLE_GROWING_FAST": return `${formatInt(num(d.rows_before))} → ${formatInt(num(d.rows_now))} rows`;
    default:
      return Object.entries(d)
        .filter(([, v]) => v != null && typeof v !== "object")
        .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "number" ? formatInt(v) : String(v)}`)
        .join(" · ");
  }
}

interface Group {
  code: string;
  severity: Severity;
  items: Insight[];
  recommendation: string | null;
}

function groupInsights(items: Insight[]): Group[] {
  const byCode = new Map<string, Insight[]>();
  for (const i of items) byCode.set(i.code, [...(byCode.get(i.code) ?? []), i]);
  return [...byCode.entries()].map(([code, list]) => {
    const severity = SEV_ORDER.find((s) => list.some((i) => i.severity === s)) ?? "info";
    const recs = new Set(list.map((i) => i.recommendation).filter(Boolean));
    return {
      code, severity,
      items: [...list].sort((a, b) => SEV_ORDER.indexOf(a.severity) - SEV_ORDER.indexOf(b.severity) || a.object_ref.localeCompare(b.object_ref)),
      // One shared recommendation is said once for the group; per-item ones stay on their rows.
      recommendation: recs.size === 1 ? [...recs][0] ?? null : null,
    };
  }).sort((a, b) => SEV_ORDER.indexOf(a.severity) - SEV_ORDER.indexOf(b.severity) || b.items.length - a.items.length);
}

function SevBadge({ s }: { s: Severity }) {
  const meta = SEV_META[s];
  return <span className="badge" data-tone={meta.tone}><Icon name={meta.icon} size={13} />{meta.label}</span>;
}

export function InsightsPage() {
  const { id = "" } = useParams();
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const list = useApi<Insight[]>(ws ? `/workspaces/${ws}/connections/${id}/insights` : null);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  function dismiss(i: Insight) {
    void api.post(`/workspaces/${ws}/insights/${i.id}/dismiss`)
      .then(() => { setNotice({ tone: "success", text: `Dismissed: ${i.title}. It stays hidden unless it changes.` }); list.reload(); })
      .catch((err: ApiError) => setNotice({ tone: "error", text: err.message }));
  }

  return (
    <>
      <ConnectionTabs />
      <header className="page-header"><div><h1>Insights</h1>
        <p>Recommendations from the catalog and statistics, worst first. A finding disappears on the next refresh once it is fixed.</p></div></header>
      {notice && <p className={`notice ${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>{notice.text}</p>}
      <AsyncView state={list} isEmpty={(l) => l.length === 0} empty="No open insights: nothing in the catalog or statistics needs attention.">
        {(items) => {
          const groups = groupInsights(items);
          const counts = SEV_ORDER.map((s) => [s, items.filter((i) => i.severity === s).length] as const).filter(([, n]) => n > 0);
          return (
            <div className="report">
              <p className="insight-summary">
                <span>{formatInt(items.length)} {items.length === 1 ? "finding" : "findings"} in {groups.length} {groups.length === 1 ? "group" : "groups"}:</span>
                {counts.map(([s, n]) => (
                  <span key={s} className="badge" data-tone={SEV_META[s].tone}><Icon name={SEV_META[s].icon} size={13} />{n} {SEV_META[s].label.toLowerCase()}</span>
                ))}
              </p>
              {groups.map((g) => {
                const title = GROUP_TITLE[g.code]?.(g.items.length) ?? (g.items.length === 1 ? g.items[0]!.title : `${g.items[0]!.title} (and ${g.items.length - 1} more)`);
                const single = g.items.length === 1 && !GROUP_TITLE[g.code];
                return (
                  <details key={g.code} className="insight-group" open={g.severity !== "info" || groups.length <= 3}>
                    <summary>
                      <Icon name="chevronRight" size={15} className="insight-chevron" />
                      <SevBadge s={g.severity} />
                      <span className="insight-title">{title}</span>
                      <code className="insight-code">{g.code}</code>
                    </summary>
                    <div className="insight-body">
                      {g.recommendation && <p className="insight-rec"><strong>Fix:</strong> {g.recommendation}</p>}
                      {single ? (
                        <div className="insight-single">
                          <p className="muted">{describeDetail(g.items[0]!) || "No further detail."} · seen {timeAgo(g.items[0]!.last_seen_at)}</p>
                          {can("editor") && <ConfirmButton small quiet label="Dismiss" confirmLabel="Dismiss finding" onConfirm={() => dismiss(g.items[0]!)} />}
                        </div>
                      ) : (
                        <div className="table-wrap">
                          <table>
                            <thead><tr><th scope="col">Object</th><th scope="col">Evidence</th>{!g.recommendation && <th scope="col">Fix</th>}<th scope="col">Last seen</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
                            <tbody>
                              {g.items.map((i) => (
                                <tr key={i.id}>
                                  <td className="mono">{i.object_ref || "connection"}</td>
                                  <td>{describeDetail(i)}</td>
                                  {!g.recommendation && <td className="hint">{i.recommendation ?? "–"}</td>}
                                  <td>{timeAgo(i.last_seen_at)}</td>
                                  <td>{can("editor") && <ConfirmButton small quiet label="Dismiss" confirmLabel="Dismiss finding" onConfirm={() => dismiss(i)} />}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  </details>
                );
              })}
            </div>
          );
        }}
      </AsyncView>
    </>
  );
}
