import { NavLink, useParams } from "react-router-dom";

import { useApi } from "../../api/useApi";
import { useLens } from "../LensContext";
import type { Connection } from "../types";
import { StateBadge } from "./StateBadge";

/** Header + tabs shared by every per-connection page. The overview passes showState={false}:
 *  its status band already leads with the state. */
export function ConnectionTabs({ showState = true }: { showState?: boolean }) {
  const { id = "" } = useParams();
  const { workspace, can } = useLens();
  const conn = useApi<Connection>(workspace ? `/workspaces/${workspace.workspace_id}/connections/${id}` : null);
  const tabs = [
    { to: `/lens/connections/${id}`, label: "Overview", end: true },
    { to: `/lens/connections/${id}/schema`, label: "Schema" },
    { to: `/lens/connections/${id}/insights`, label: "Insights" },
    { to: `/lens/connections/${id}/dashboard`, label: "Dashboard" },
    { to: `/lens/connections/${id}/alerts`, label: "Alerts" },
    ...(can("editor") ? [{ to: `/lens/connections/${id}/query`, label: "Query" }] : []),
    // The demo warehouse ships with the reports built from it: proof of what Lens data can become.
    ...(conn.data?.is_demo ? [{ to: "/overview", label: "Reports" }] : []),
  ];
  return (
    <div className="conn-tabs-wrap">
      {conn.data && (
        <p className="conn-tabs-title">
          <NavLink to="/lens">Databases</NavLink> / <strong>{conn.data.name}</strong>
          {showState && <> <StateBadge state={conn.data.current_state} /></>}
        </p>
      )}
      <nav className="conn-tabs" aria-label="Connection sections">
        {tabs.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end}>{t.label}</NavLink>
        ))}
      </nav>
    </div>
  );
}
