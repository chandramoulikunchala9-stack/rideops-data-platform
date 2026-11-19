import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import type { Health } from "../api/types";
import { useApi } from "../api/useApi";

const LINKS = [
  { to: "/", label: "Overview", end: true },
  { to: "/analytics", label: "Business analytics" },
  { to: "/pipelines", label: "Pipelines" },
  { to: "/data-quality", label: "Data quality" },
  { to: "/monitoring", label: "Alerts & incidents" },
  { to: "/costs", label: "Cloud costs" },
  { to: "/settings", label: "Settings & access" },
];

type Theme = "light" | "dark" | "system";

function readTheme(): Theme {
  try {
    return (localStorage.getItem("rideops-theme") as Theme | null) ?? "system";
  } catch {
    return "system"; // storage can be blocked (private mode); the default still works
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

  const apiOk = health.data?.status === "ok";
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">R</span>
          RideOps
        </div>
        <nav className="nav" aria-label="Main">
          {LINKS.map((link) => (
            <NavLink key={link.to} to={link.to} end={link.end}>
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="field" style={{ marginBottom: 12 }}>
            <label htmlFor="theme">Theme</label>
            <select id="theme" value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </div>
          <div role="status">
            API:{" "}
            {health.loading ? "checking…" : apiOk ? "● connected" : "✕ unavailable"}
            {health.data && ` · v${health.data.version} · ${health.data.environment}`}
          </div>
          <div style={{ marginTop: 6 }}>All business data is synthetic.</div>
        </div>
      </aside>
      <main id="main" className="main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
