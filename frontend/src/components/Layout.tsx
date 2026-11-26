import { useEffect, useState } from "react";
import { matchPath, NavLink, Outlet, useLocation } from "react-router-dom";

import type { Health } from "../api/types";
import { useApi } from "../api/useApi";
import { useLens } from "../lens/LensContext";
import { STATE_META, STATE_ORDER } from "../lens/status";
import type { Connection } from "../lens/types";
import { Icon, type IconName } from "./Icon";

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  end?: boolean;
}

// The demo warehouse's own reports (the former RideOps dashboard). They are sample output, not the
// product, so they appear only while the demo connection or one of these pages is open.
const DEMO_REPORTS: NavItem[] = [
  { to: "/overview", label: "Overview", icon: "overview" },
  { to: "/analytics", label: "Business analytics", icon: "analytics" },
  { to: "/sources", label: "Source apps", icon: "rider" },
  { to: "/pipelines", label: "Pipelines", icon: "pipelines" },
  { to: "/data-quality", label: "Data quality", icon: "quality" },
  { to: "/monitoring", label: "Pipeline alerts", icon: "warning" },
  { to: "/costs", label: "Cloud costs", icon: "costs" },
  { to: "/settings", label: "Access", icon: "settings" },
];
const DEMO_PATHS = DEMO_REPORTS.map((item) => `${item.to}/*`);
const NAV_CONNECTIONS = 6; // the rest are one click away on Databases

type Theme = "light" | "dark" | "system";

// Light by default, like the landing page; dark and "follow the system" stay one click away.
function readTheme(): Theme {
  try {
    return (localStorage.getItem("rideops-theme") as Theme | null) ?? "light";
  } catch {
    return "light"; // storage can be blocked (private mode); the default still works
  }
}

function worstFirst(a: Connection, b: Connection): number {
  return STATE_ORDER.indexOf(a.current_state) - STATE_ORDER.indexOf(b.current_state) || a.name.localeCompare(b.name);
}

export function Layout() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  const { pathname } = useLocation();
  const health = useApi<Health>("/health");
  const lens = useLens();
  const signedIn = lens.status === "signed_in";
  const lensOff = lens.status === "disabled";
  const ws = signedIn ? lens.workspace?.workspace_id : undefined;
  const notifications = useApi<{ unread: number }>(signedIn ? "/notifications?unread=true" : null);
  const connections = useApi<Connection[]>(ws ? `/workspaces/${ws}/connections` : null);
  const unread = notifications.data?.unread ?? 0;
  const { reload: reloadNotifications } = notifications;
  const { reload: reloadConnections } = connections;
  useEffect(() => {
    if (!signedIn) return;
    const t = window.setInterval(() => {
      reloadNotifications();
      reloadConnections();
    }, 60_000);
    return () => window.clearInterval(t);
  }, [signedIn, reloadNotifications, reloadConnections]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("rideops-theme", theme);
    } catch {
      /* ignore: preference just won't persist */
    }
  }, [theme]);

  // On a phone the nav is one horizontal strip; keep the current page's item in view.
  useEffect(() => {
    document.querySelector(".nav a.active")?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [pathname]);

  const list = [...(connections.data ?? [])].sort(worstFirst);
  const openConnection = matchPath("/lens/connections/:id/*", pathname)?.params.id;
  const onDemoConnection = list.some((c) => c.is_demo && c.id === openConnection);
  const onDemoReport = DEMO_PATHS.some((p) => matchPath(p, pathname));
  const showDemo = lensOff || onDemoReport || onDemoConnection;
  const alertsActive = ["/lens/alerts", "/lens/channels"].some((p) => pathname.startsWith(p));

  const apiState = health.loading && !health.data ? "checking" : health.data?.status === "ok" ? "ok" : "down";
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        {/* A plain link: the landing page lives outside the dashboard's router (basename /app). */}
        <a className="brand" href="/">
          <span className="brand-name">Lens</span>
          <span className="brand-sub">Database status and analytics</span>
        </a>
        <nav className="nav" aria-label="Main">
          {!lensOff && (
            <div className="nav-group">
              <NavLink to="/lens" end>
                <Icon name="sources" />
                Databases
              </NavLink>
              {list.length > 0 && (
                <ul className="nav-connections" aria-label="Connections">
                  {list.slice(0, NAV_CONNECTIONS).map((c) => {
                    const meta = STATE_META[c.current_state];
                    return (
                      <li key={c.id}>
                        <NavLink to={`/lens/connections/${c.id}`} title={`${c.name}: ${meta.label}`}>
                          <span className="state-dot" data-tone={meta.tone} aria-hidden="true" />
                          <span className="nav-connection-name">{c.name}</span>
                          <span className="sr-only">, {meta.label}</span>
                        </NavLink>
                      </li>
                    );
                  })}
                  {list.length > NAV_CONNECTIONS && (
                    <li className="nav-more">
                      <NavLink to="/lens" end>All {list.length} databases</NavLink>
                    </li>
                  )}
                </ul>
              )}
              {signedIn && (
                <>
                  <NavLink to="/lens/alerts" className={alertsActive ? "active" : undefined}>
                    <Icon name="alerts" />
                    Alerts
                  </NavLink>
                  <NavLink to="/lens/incidents">
                    <Icon name="warning" />
                    Incidents
                  </NavLink>
                  <NavLink to="/lens/dashboards">
                    <Icon name="analytics" />
                    Dashboards
                  </NavLink>
                  <NavLink to="/lens/notifications">
                    <Icon name="activity" />
                    Notifications
                    {unread > 0 && <span className="count-badge" aria-label={`${unread} unread`}>{unread}</span>}
                  </NavLink>
                </>
              )}
            </div>
          )}
          {showDemo && (
            <div className="nav-group nav-demo" role="group" aria-labelledby="nav-demo-label">
              <p className="nav-group-label" id="nav-demo-label">
                Demo warehouse <span className="nav-demo-note">synthetic data</span>
              </p>
              {DEMO_REPORTS.map((item) => (
                <NavLink key={item.to} to={item.to}>
                  <Icon name={item.icon} />
                  {item.label}
                </NavLink>
              ))}
            </div>
          )}
        </nav>
        <div className="sidebar-footer">
          {signedIn && lens.me && (
            <div className="account">
              <span className="account-name">{lens.me.user.display_name}</span>
              {lens.me.memberships.length > 1 ? (
                <div className="field account-workspace">
                  <label htmlFor="workspace">Workspace</label>
                  <select id="workspace" value={lens.workspace?.workspace_id ?? ""}
                          onChange={(e) => lens.setWorkspace(e.target.value)}>
                    {lens.me.memberships.map((m) => (
                      <option key={m.workspace_id} value={m.workspace_id}>{m.name} ({m.role})</option>
                    ))}
                  </select>
                </div>
              ) : (
                <span className="api-meta account-workspace">{lens.workspace?.name} · {lens.workspace?.role}</span>
              )}
              <button type="button" className="btn small" onClick={() => void lens.signOut()}>Log out</button>
            </div>
          )}
          {lens.status === "signed_out" && (
            <NavLink className="btn small" to="/login">Log in</NavLink>
          )}
          <div className="api-status" role="status" data-state={apiState}
               title={apiState === "ok" ? "API connected" : apiState === "down" ? "API unavailable" : "Checking API"}>
            <span className="api-dot" aria-hidden="true" />
            <span className="api-text">
              {apiState === "checking" ? "Checking API…" : apiState === "ok" ? "API connected" : "API unavailable"}
              {health.data && (
                <span className="api-meta">
                  v{health.data.version} · {health.data.environment}
                </span>
              )}
            </span>
          </div>
          <div className="field theme-field">
            <label htmlFor="theme">Theme</label>
            <select id="theme" value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
              <option value="system">System</option>
            </select>
          </div>
        </div>
      </aside>
      <main id="main" className="main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
