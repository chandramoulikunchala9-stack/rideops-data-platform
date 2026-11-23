import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import type { Health } from "../api/types";
import { useApi } from "../api/useApi";
import { Icon, type IconName } from "./Icon";

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  end?: boolean;
}

// Grouped the way the team works: read the business, check the data, run the platform.
const GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Reports",
    items: [
      { to: "/", label: "Overview", icon: "overview", end: true },
      { to: "/analytics", label: "Business analytics", icon: "analytics" },
    ],
  },
  {
    label: "Data",
    items: [
      { to: "/sources", label: "Source apps", icon: "sources" },
      { to: "/pipelines", label: "Pipelines", icon: "pipelines" },
      { to: "/data-quality", label: "Data quality", icon: "quality" },
    ],
  },
  {
    label: "Operations",
    items: [
      { to: "/monitoring", label: "Alerts & incidents", icon: "alerts" },
      { to: "/costs", label: "Cloud costs", icon: "costs" },
    ],
  },
];

type Theme = "light" | "dark" | "system";

// Light by default, like the landing page; dark and "follow the system" stay one click away.
function readTheme(): Theme {
  try {
    return (localStorage.getItem("rideops-theme") as Theme | null) ?? "light";
  } catch {
    return "light"; // storage can be blocked (private mode); the default still works
  }
}

export function Layout() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  const health = useApi<Health>("/health");

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

  const apiState = health.loading && !health.data ? "checking" : health.data?.status === "ok" ? "ok" : "down";
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        {/* A plain link: the landing page lives outside the dashboard's router (basename /app). */}
        <a className="brand" href="/">
          <span className="brand-name">RideOps</span>
          <span className="brand-sub">Transport data platform</span>
        </a>
        <span className="mobile-note">Synthetic data</span>
        <nav className="nav" aria-label="Main">
          {GROUPS.map((group) => (
            <div className="nav-group" key={group.label}>
              <p className="nav-group-label">{group.label}</p>
              {group.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.end}>
                  <Icon name={item.icon} />
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
          <div className="nav-group">
            <NavLink to="/settings">
              <Icon name="settings" />
              Settings & access
            </NavLink>
          </div>
        </nav>
        <div className="sidebar-footer">
          <div className="api-status" role="status" data-state={apiState}>
            <span className="api-dot" aria-hidden="true" />
            <span>
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
          <p className="synthetic-note">All business data is synthetic.</p>
        </div>
      </aside>
      <main id="main" className="main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
