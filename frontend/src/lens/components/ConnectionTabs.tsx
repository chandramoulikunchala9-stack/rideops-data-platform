import { NavLink, useParams } from "react-router-dom";

import { useApi } from "../../api/useApi";
import { useLens } from "../LensContext";
import type { Connection } from "../types";
import { StateBadge } from "./StateBadge";

/** Header + tabs shared by every per-connection page (wireframes §7). */
export function ConnectionTabs() {
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
  ];
  return (
    <div className="conn-tabs-wrap">
      {conn.data && (
        <p className="conn-tabs-title">
          <NavLink to="/lens">Databases</NavLink> / <strong>{conn.data.name}</strong>{" "}
          <StateBadge state={conn.data.current_state} />
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
