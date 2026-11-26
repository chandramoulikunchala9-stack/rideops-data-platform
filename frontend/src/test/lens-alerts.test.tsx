import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "../App";
import type { AlertEvent, Me } from "../lens/types";

const WS = "11111111-1111-1111-1111-111111111111";
const me: Me = {
  user: { id: "u1", email: "asha@example.com", display_name: "Asha", email_verified: true },
  memberships: [{ workspace_id: WS, name: "Asha's workspace", slug: "asha", role: "owner" }],
  current_workspace_id: WS,
  csrf_token: "csrf",
};

function stubApi(routes: Record<string, unknown>) {
  const calls: { method: string; path: string; body: unknown; headers: Record<string, string> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace("/api/v1", "").split("?")[0] ?? "";
    const method = init?.method ?? "GET";
    let body: unknown = init?.body;
    if (typeof body === "string") body = JSON.parse(body);
    calls.push({ method, path, body, headers: (init?.headers ?? {}) as Record<string, string> });
    const r = routes[`${method} ${path}`] ?? routes[path];
    return new Response(JSON.stringify(r ?? []), { status: 200 });
  }));
  return calls;
}

const renderAt = (path: string) => render(<MemoryRouter initialEntries={[path]}><AppRoutes /></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());

const alert: AlertEvent = {
  id: 7, status: "firing", severity: "critical", summary: "Orders DB is unreachable for 2 checks (CONN_TIMEOUT)",
  opened_at: new Date().toISOString(), acknowledged_at: null, resolved_at: null, muted_until: null,
  connection_id: "c1", connection_name: "Orders DB", incident_id: "i1", rule_name: "Database down", runbook_url: null,
};

describe("Lens alerts", () => {
  it("lists open alerts and acknowledges one", async () => {
    const calls = stubApi({ "/auth/me": me, [`/workspaces/${WS}/alerts`]: [alert], "/notifications": { unread: 3, items: [] } });
    const user = userEvent.setup();
    renderAt("/lens/alerts");
    expect(await screen.findByText("Database down")).toBeInTheDocument();
    expect(screen.getByText(/unreachable for 2 checks/)).toBeInTheDocument();
    expect(await screen.findByLabelText("3 unread")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `/workspaces/${WS}/alerts/7/ack`)).toBe(true));
  });

  it("shows a webhook signing secret once, right after creating the channel", async () => {
    stubApi({
      "/auth/me": me, [`/workspaces/${WS}/notification-channels`]: [],
      [`POST /workspaces/${WS}/notification-channels`]: { id: "ch1", kind: "webhook", name: "Ops hook", summary: "https://hooks.example.com/…",
        is_verified: false, created_at: "2026-11-24T00:00:00Z", signing_secret: "s3cr3t-signing-value" },
    });
    const user = userEvent.setup();
    renderAt("/lens/channels");
    await user.type(await screen.findByLabelText("Name"), "Ops hook");
    await user.type(screen.getByLabelText("HTTPS URL"), "https://hooks.example.com/lens");
    await user.click(screen.getByRole("button", { name: "Add channel" }));
    expect(await screen.findByText("s3cr3t-signing-value")).toBeInTheDocument();
    expect(screen.getByText(/shown once/)).toBeInTheDocument();
  });

  it("uploads a SQLite file in the wizard and tests it without network stages", async () => {
    const calls = stubApi({
      "/auth/me": me,
      "/meta/engines": { engines: [{ engine: "sqlite", label: "SQLite (file upload)", default_port: null, release: "v1", example: null, available: true, capabilities: [] }], egress_ips: [] },
      [`POST /workspaces/${WS}/uploads/sqlite`]: { upload_id: "22222222-2222-2222-2222-222222222222", filename: "shop.sqlite", bytes: 8192 },
      [`POST /workspaces/${WS}/connections/test`]: { test_id: "t1", ok: true, server_version: "SQLite 3.45", capabilities: [], privilege_findings: [], grant_script: null,
        stages: [{ stage: "DB_SELECT", ok: true, ms: 1, message: "1 tables and views", code: null, warning: false }] },
    });
    const user = userEvent.setup();
    renderAt("/lens/connections/new");
    await user.click(await screen.findByRole("button", { name: /SQLite/ }));
    await user.upload(screen.getByLabelText(/SQLite file/), new File(["SQLite format 3\0"], "shop.sqlite"));
    await waitFor(() => expect(screen.getByLabelText("Display name *")).toHaveValue("shop"));
    await user.click(screen.getByRole("button", { name: "Next →" }));
    await user.click(screen.getByRole("button", { name: /Test connection/ }));
    expect(await screen.findByText("1 tables and views")).toBeInTheDocument();
    const upload = calls.find((c) => c.path.endsWith("/uploads/sqlite"))!;
    expect(upload.headers["X-Filename"]).toBe("shop.sqlite");
    expect(calls.find((c) => c.path.endsWith("/connections/test"))!.body).toEqual({ engine: "sqlite", database: "22222222-2222-2222-2222-222222222222" });
  });
});
