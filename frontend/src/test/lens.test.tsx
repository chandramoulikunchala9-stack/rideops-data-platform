import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "../App";
import type { Connection, ConnectionStatus, Me, TestResult } from "../lens/types";

const WS = "11111111-1111-1111-1111-111111111111";

const me: Me = {
  user: { id: "u1", email: "asha@example.com", display_name: "Asha", email_verified: true },
  memberships: [{ workspace_id: WS, name: "Asha's workspace", slug: "asha", role: "owner" }],
  current_workspace_id: WS,
  csrf_token: "csrf",
};

function conn(overrides: Partial<Connection>): Connection {
  return {
    id: "c1", name: "shop-prod", engine: "postgres", environment: "prod", host: "db.example.com", port: 5432,
    database: "shop", username: "lens_ro", display_url: "postgresql://lens_ro@db.example.com:5432/shop",
    tls_mode: "verify-full", tags: ["ecommerce"], color: null, check_interval_s: 300, is_paused: false,
    is_demo: false, current_state: "healthy", health_score: 94, server_version: "PostgreSQL 17.5",
    capabilities: ["status_basic"], last_checked_at: new Date().toISOString(), last_state_change_at: null,
    created_at: "2026-11-20T00:00:00Z", sparkline: [40, 42, 41], ...overrides,
  };
}

type Route = { status?: number; body: unknown };

function stubApi(routes: Record<string, Route | ((body: unknown) => Route)>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace("/api/v1", "").split("?")[0] ?? "";
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body });
    const route = routes[`${method} ${path}`] ?? routes[path];
    if (!route) return new Response(JSON.stringify([]), { status: 200 });
    const r = typeof route === "function" ? route(body) : route;
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

const unauthorized: Route = { status: 401, body: { error: { code: "UNAUTHENTICATED", message: "Sign in to continue." } } };

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Lens", () => {
  it("sends signed-out visitors to the login page", async () => {
    stubApi({ "/auth/me": unauthorized });
    renderAt("/lens");
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });

  it("explains when Lens is switched off on the server", async () => {
    stubApi({ "/auth/me": { status: 404, body: { detail: "Not Found" } } });
    renderAt("/lens");
    expect(await screen.findByRole("heading", { name: "Lens is not enabled" })).toBeInTheDocument();
  });

  it("logs in and shows the fleet, worst state first", async () => {
    let signedIn = false;
    stubApi({
      "/auth/me": () => (signedIn ? { body: me } : unauthorized),
      "POST /auth/login": () => {
        signedIn = true;
        return { body: me };
      },
      [`/workspaces/${WS}/connections`]: {
        body: [conn({}), conn({ id: "c2", name: "orders-legacy", current_state: "unreachable", health_score: null, sparkline: [] })],
      },
      [`/workspaces/${WS}/status/overview`]: {
        body: { total: 2, by_state: { healthy: 1, unreachable: 1 }, worst: [], egress_ips: ["203.0.113.5"] },
      },
    });
    const user = userEvent.setup();
    renderAt("/login?next=/lens");
    await user.type(await screen.findByLabelText("Email"), "asha@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery-1");
    await user.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("heading", { name: "Databases" })).toBeInTheDocument();
    const rows = await screen.findAllByRole("row");
    expect(within(rows[1]!).getByText("orders-legacy")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Unreachable")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("203.0.113.5")).toBeInTheDocument();
  });

  it("walks the add-connection wizard: paste URL → details → test → save", async () => {
    const test: TestResult = {
      test_id: "t1", ok: true, server_version: "PostgreSQL 17.5", capabilities: ["status_basic"],
      privilege_findings: [{ privilege: "INSERT", object: "public.*", severity: "warning" }],
      grant_script: "CREATE ROLE lens_ro LOGIN;",
      stages: [
        { stage: "DNS", ok: true, ms: 3, message: "db.example.com → 93.184.216.34", code: null, warning: false },
        { stage: "NETWORK_POLICY", ok: true, ms: 0, message: "Public address", code: null, warning: false },
        { stage: "TCP", ok: true, ms: 20, message: "", code: null, warning: false },
        { stage: "PRIVILEGES", ok: false, ms: 5, message: "Role can write (INSERT).", code: "PRIV_WRITE_GRANTED", warning: true },
      ],
    };
    const calls = stubApi({
      "/auth/me": { body: me },
      "/meta/engines": { body: { engines: [{ engine: "postgres", label: "PostgreSQL", default_port: 5432, release: "v1", example: null, available: true, capabilities: [] }], egress_ips: [] } },
      [`/workspaces/${WS}`]: { body: { settings: { allow_insecure_connections: false } } },
      [`POST /workspaces/${WS}/connections/parse-url`]: {
        body: { engine: "postgres", host: "db.example.com", port: 5432, database: "shop", username: "lens_ro", options: {},
                tls_mode: "verify-full", has_password: true, display_url: "postgresql://lens_ro@db.example.com:5432/shop" },
      },
      [`POST /workspaces/${WS}/connections/test`]: { body: test },
      [`POST /workspaces/${WS}/connections`]: { status: 201, body: conn({ id: "new1" }) },
      [`/workspaces/${WS}/connections/new1/status`]: { body: { connection: conn({ id: "new1" }), latest: null, uptime_24h: null } },
    });
    const user = userEvent.setup();
    renderAt("/lens/connections/new");
    await user.type(await screen.findByLabelText("Connection URL"), "postgresql://lens_ro:s3cret@db.example.com:5432/shop");
    await user.click(screen.getByRole("button", { name: "Continue with URL" }));
    expect(await screen.findByLabelText("Host *")).toHaveValue("db.example.com");
    expect(screen.getByLabelText("Password")).toHaveValue("s3cret");
    await user.click(screen.getByRole("button", { name: "Next →" }));
    expect(screen.getByLabelText(/Verify full/)).toBeChecked();
    expect(screen.getByLabelText(/Disabled \(blocked/)).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next →" }));
    await user.click(screen.getByRole("button", { name: /Test connection/ }));
    expect(await screen.findByText("Role can write (INSERT).")).toBeInTheDocument();
    expect(screen.getByText(/Show the SQL for a read-only role/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `/workspaces/${WS}/connections`)).toBe(true));
    const save = calls.find((c) => c.method === "POST" && c.path === `/workspaces/${WS}/connections`)!;
    expect(save.body).toMatchObject({ test_id: "t1", draft: { host: "db.example.com", secret: { password: "s3cret" } } });
    // The URL with the password was never sent anywhere except the parse call.
    expect(calls.filter((c) => JSON.stringify(c.body ?? "").includes("s3cret@")).map((c) => c.path))
      .toEqual([`/workspaces/${WS}/connections/parse-url`]);
  });

  it("shows a connection's state, prod banner and score breakdown", async () => {
    const status: ConnectionStatus = {
      connection: conn({}),
      uptime_24h: 99.9,
      latest: {
        taken_at: new Date().toISOString(), state: "healthy", reachable: true, latency_ms: 42, error_code: null,
        error_message: null, server_version: "PostgreSQL 17.5", uptime_s: 3600, db_size_bytes: 5 * 1024 ** 3,
        health_score: 94, score_breakdown: [{ component: "long_queries", points: 5, reason: "1 queries running > 5 min" }],
        metrics: { "connections.total": 23, "connections.max": 100, "connections.used_pct": 23, "cache.hit_ratio": 0.994 },
        details: { long_running: [] }, unavailable: { "queries.long_running.text": "needs pg_monitor role" },
      },
    };
    stubApi({
      "/auth/me": { body: me },
      [`/workspaces/${WS}/connections/c1/status`]: { body: status },
      [`/workspaces/${WS}/connections/c1/uptime`]: { body: { window: "24h", uptime_pct: 99.9, checks: 288, bars: [] } },
      [`/workspaces/${WS}/connections/c1/status/history`]: { body: [] },
    });
    renderAt("/lens/connections/c1");
    expect(await screen.findByRole("heading", { name: /shop-prod/ })).toBeInTheDocument();
    expect(screen.getByText(/Production database: Lens reads only, never writes/)).toBeInTheDocument();
    expect(screen.getByText("1 queries running > 5 min")).toBeInTheDocument();
    expect(screen.getByText("23 / 100")).toBeInTheDocument();
    expect(screen.getByText("5.0 GB")).toBeInTheDocument();
    expect(screen.getByText(/needs pg_monitor role/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument(); // owner
  });
});
