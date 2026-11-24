import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "../App";
import type { Catalog, Me, ProfileResponse, QueryResult } from "../lens/types";

const WS = "11111111-1111-1111-1111-111111111111";
const C = "c1";
const me: Me = {
  user: { id: "u1", email: "asha@example.com", display_name: "Asha", email_verified: true },
  memberships: [{ workspace_id: WS, name: "Asha's workspace", slug: "asha", role: "editor" }],
  current_workspace_id: WS,
  csrf_token: "csrf",
};
const connection = { id: C, name: "shop-prod", current_state: "healthy" };

function stubApi(routes: Record<string, unknown>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace("/api/v1", "").split("?")[0] ?? "";
    const method = init?.method ?? "GET";
    calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const body = routes[`${method} ${path}`] ?? routes[path];
    return new Response(JSON.stringify(body ?? []), { status: 200 });
  }));
  return calls;
}

const renderAt = (path: string) => render(<MemoryRouter initialEntries={[path]}><AppRoutes /></MemoryRouter>);
const base = `/workspaces/${WS}/connections/${C}`;

afterEach(() => vi.unstubAllGlobals());

describe("Lens analytics", () => {
  it("lists the catalog with PII and missing-key notes", async () => {
    const catalog: Catalog = {
      last_sync: { started_at: new Date().toISOString(), finished_at: new Date().toISOString(), ok: true, error: null },
      schemas: ["shop"], total_bytes: 2048,
      tables: [
        { schema_name: "shop", table_name: "customers", kind: "table", comment: null, primary_key: ["id"], freshness_column: "updated_at",
          pii_columns: ["email", "phone"], is_pinned: false, column_count: 7, row_estimate: 2000, total_bytes: 1024,
          freshness_max: new Date().toISOString(), matching_columns: [] },
        { schema_name: "shop", table_name: "events_log", kind: "table", comment: null, primary_key: null, freshness_column: null,
          pii_columns: [], is_pinned: false, column_count: 2, row_estimate: 50, total_bytes: 1024, freshness_max: null, matching_columns: [] },
      ],
    };
    stubApi({ "/auth/me": me, [base]: connection, [`${base}/catalog`]: catalog });
    renderAt(`/lens/connections/${C}/schema`);
    expect(await screen.findByRole("link", { name: /customers/ })).toBeInTheDocument();
    expect(screen.getByText("PII ×2")).toBeInTheDocument();
    const row = screen.getByRole("link", { name: /events_log/ }).closest("tr")!;
    expect(within(row).getByText("no PK")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Connection sections" })).toBeInTheDocument();
  });

  it("shows a column profile with auto-charts and hides PII values", async () => {
    const profile: ProfileResponse = {
      pending: false,
      profile: {
        profiled_at: "2026-11-24T00:00:00Z", sample_rows: 2000, method: "first 10000 rows", elapsed_ms: 12,
        profiles: [
          { column: "status", data_type: "text", kind: "text", sample_size: 2000, null_pct: 0, distinct_est: 3, pii: false,
            top_k: [["completed", 700], ["cancelled", 650], ["refunded", 650]] },
          { column: "email", data_type: "text", kind: "text", sample_size: 2000, null_pct: 0, distinct_est: 2000, pii: true,
            length: { min: 16, max: 20, mean: 18 } },
        ],
        charts: [{ column: "status", type: "bar", title: "Top values of status", bars: [["completed", 700], ["cancelled", 650]] }],
      },
    };
    stubApi({
      "/auth/me": me, [base]: connection,
      [`${base}/tables/shop/orders`]: { table: { schema_name: "shop", table_name: "orders", kind: "table", comment: null, columns: [],
        indexes: [], foreign_keys: [], primary_key: ["id"], freshness_column: null, pii_columns: [], is_pinned: false },
        stats: null, changes: [], referenced_by: [] },
      [`${base}/tables/shop/orders/profile`]: profile,
    });
    const user = userEvent.setup();
    renderAt(`/lens/connections/${C}/tables/shop/orders`);
    await user.click(await screen.findByRole("tab", { name: "Profile" }));
    expect(await screen.findByRole("list", { name: "Top values of status" })).toBeInTheDocument();
    const email = screen.getByRole("region", { name: "Profile of email" });
    expect(within(email).getByText("PII")).toBeInTheDocument();
    expect(within(email).getByText("16–20")).toBeInTheDocument();
  });

  it("validates, runs a query and shows masked columns", async () => {
    const result: QueryResult = {
      run_id: "r1", kind: "select", columns: [{ name: "id", type: "int8" }, { name: "email", type: "text" }],
      rows: [[1, "•••"], [2, "•••"]], row_count: 2, truncated: false, elapsed_ms: 4, masked_columns: ["email"],
    };
    const calls = stubApi({
      "/auth/me": me, [base]: connection,
      [`POST ${base}/queries/validate`]: { ok: true, kind: "select", code: null, message: null, tables: ["shop.customers"] },
      [`POST ${base}/queries/run`]: result,
      [`/workspaces/${WS}/saved-queries`]: [],
    });
    const user = userEvent.setup();
    renderAt(`/lens/connections/${C}/query`);
    const editor = await screen.findByLabelText("SQL");
    await user.clear(editor);
    await user.type(editor, "select id, email from shop.customers");
    expect(await screen.findByText(/Read-only select · reads shop.customers/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Run/ }));
    expect(await screen.findByText("2 rows")).toBeInTheDocument();
    expect(screen.getByText(/masked: email/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Export CSV" })).toHaveAttribute("href", `/api/v1/workspaces/${WS}/query-runs/r1/export.csv`);
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/queries/run"))?.body).toMatchObject({ max_rows: 1000, reveal: false }));
  });

  it("renders a dashboard widget from its cached result", async () => {
    stubApi({
      "/auth/me": me,
      [`/workspaces/${WS}/dashboards/d1`]: {
        id: "d1", name: "Ops", description: null, refresh_interval_s: 900, last_refreshed_at: "2026-11-24T00:00:00Z", created_at: "2026-11-24T00:00:00Z",
        widgets: [{ id: "w1", kind: "bar", title: "Orders by status", position: 0, source: { type: "saved_query", id: "q1" },
          options: { x: "status", y: "n" }, query_name: "Orders", refreshed_at: null, last_status: "ok", last_error: null, stale: false,
          result_cache: { columns: [{ name: "status" }, { name: "n" }], rows: [["completed", 7], ["refunded", 3]], row_count: 2, truncated: false } }],
      },
    });
    renderAt("/lens/dashboards/d1");
    const chart = await screen.findByRole("list", { name: "Orders by status" });
    expect(within(chart).getByText("completed")).toBeInTheDocument();
    expect(within(chart).getByText("7")).toBeInTheDocument();
  });
});
