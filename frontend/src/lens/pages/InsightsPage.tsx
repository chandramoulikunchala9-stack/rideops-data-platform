import { useParams } from "react-router-dom";

import { api } from "../../api/client";
import { useApi } from "../../api/useApi";
import { AsyncView } from "../../components/AsyncView";
import { Card } from "../../components/Card";
import { timeAgo } from "../status";
import { useLens } from "../LensContext";
import { ConnectionTabs } from "../components/ConnectionTabs";
import type { Insight } from "../types";

const TONE = { critical: "critical", warning: "warning", info: "neutral" } as const;

/** Advisor findings (§16.4), recomputed after every catalog/stats refresh. */
export function InsightsPage() {
  const { id = "" } = useParams();
  const { workspace, can } = useLens();
  const ws = workspace?.workspace_id ?? "";
  const list = useApi<Insight[]>(ws ? `/workspaces/${ws}/connections/${id}/insights` : null);
  return (
    <>
      <ConnectionTabs />
      <header className="page-header"><div><h1>Insights</h1><p>Recommendations from the catalog and statistics. Resolved findings disappear on the next refresh.</p></div></header>
      <AsyncView state={list} isEmpty={(l) => l.length === 0} empty="No open insights. 👍">
        {(items) => (
          <div className="stack">
            {items.map((i) => (
              <Card key={i.id} title={i.title} subtitle={<><span className="badge" data-tone={TONE[i.severity]}>{i.severity}</span> <code>{i.code}</code> · seen {timeAgo(i.last_seen_at)}</>}
                    actions={can("editor") && <button className="btn small" type="button" onClick={() => void api.post(`/workspaces/${ws}/insights/${i.id}/dismiss`).then(list.reload)}>Dismiss</button>}>
                {i.recommendation && <p>{i.recommendation}</p>}
                {Object.keys(i.detail).length > 0 && <p className="hint mono">{JSON.stringify(i.detail)}</p>}
              </Card>
            ))}
          </div>
        )}
      </AsyncView>
    </>
  );
}
