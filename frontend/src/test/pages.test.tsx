import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "../App";
import type { Overview, PipelineSummary } from "../api/types";

const overview: Overview = {
  period: { start: "2026-10-02", end: "2026-10-31" },
  data_as_of: "2026-10-31",
  total_bookings: 39759,
  completed_rides: 30831,
  total_revenue_inr: 5218500,
  cancellation_rate: 0.171,
  active_drivers: 1890,
  active_customers: 8800,
  pipeline_success_rate: 0.95,
  failed_runs_30d: 7,
  recent_failures: [
    { run_id: "RUN-1", pipeline_name: "ingest_payments", status: "FAILED", start_ts: "2026-10-19T00:04:00Z", error_message: "schema mismatch" },
  ],
  cloud_spend: { month: "2026-10", amount_usd: 1218.26, sources: ["simulated"] },
};

const pipelines: PipelineSummary[] = [
  {
    pipeline_name: "pg_quality_checks", layer: "silver", last_run_id: "R1", last_status: "SUCCESS",
    last_run_at: "2026-11-19T06:00:00Z", last_duration_sec: 2, last_error: null, runs: 3, success_rate: 1,
    last_success_at: null, p50_duration_sec: 2, run_sources: ["local_run"], retryable: true,
  },
  {
    pipeline_name: "ingest_payments", layer: "bronze", last_run_id: "R2", last_status: "FAILED",
    last_run_at: "2026-10-19T00:04:00Z", last_duration_sec: 60, last_error: "AnalysisException: merge failed",
    runs: 30, success_rate: 0.9, last_success_at: null, p50_duration_sec: 420, run_sources: ["simulated_history"],
    retryable: false,
  },
];

function stubApi(routes: Record<string, { status?: number; body: unknown }>) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace("/api/v1", "").split("?")[0] ?? "";
    const key = `${init?.method ?? "GET"} ${path}`;
    const route = routes[key] ?? routes[path];
    if (!route) return new Response(JSON.stringify([]), { status: 200 });
    return new Response(JSON.stringify(route.body), { status: route.status ?? 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Overview page", () => {
  it("shows KPIs from the API and labels simulated cost", async () => {
    stubApi({
      "/analytics/overview": { body: overview },
      "/analytics/revenue": { body: { period: overview.period, granularity: "day", series: [], by_city: [] } },
      "/monitoring/alerts": { body: [] },
      "/health": { body: { status: "ok", version: "0.1.0", environment: "local" } },
    });
    renderAt("/");
    expect(await screen.findByText("39,759")).toBeInTheDocument();
    expect(screen.getByText("17.1%")).toBeInTheDocument();
    expect(screen.getByText("Simulated")).toBeInTheDocument();
    // getByText, not getByRole: role queries walk the whole (large) report page and are slow in jsdom.
    expect(screen.getByText("ingest_payments").closest("a")).toHaveAttribute("href", "/pipelines/ingest_payments?run=RUN-1");
    // The Overview report renders three bands, two charts and a map; jsdom needs more than the default 5 s
    // when the test files run in parallel.
  }, 15_000);

  it("passes the date filter from the URL to the API", async () => {
    const fetchMock = stubApi({
      "/analytics/overview": { body: overview },
      "/analytics/revenue": { body: { period: overview.period, granularity: "day", series: [], by_city: [] } },
    });
    renderAt("/?start_date=2026-10-10&end_date=2026-10-20");
    await screen.findByText("39,759");
    expect(fetchMock.mock.calls.map((c) => String(c[0]))).toContain(
      "/api/v1/analytics/overview?start_date=2026-10-10&end_date=2026-10-20",
    );
  });
});

describe("Pipelines page", () => {
  it("retries a local pipeline and reports the API response", async () => {
    const fetchMock = stubApi({
      "/pipelines": { body: pipelines },
      "POST /pipelines/pg_quality_checks/retry": {
        status: 202,
        body: { accepted: true, pipeline_name: "pg_quality_checks", retry_of_run_id: "R1", message: "Retry started." },
      },
    });
    renderAt("/pipelines");
    const row = (await screen.findByRole("link", { name: "pg_quality_checks" })).closest("tr") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Retry started.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/pipelines/pg_quality_checks/retry", expect.objectContaining({ method: "POST" }));
  });

  it("shows the server's reason when a retry is refused", async () => {
    stubApi({
      "/pipelines": { body: pipelines },
      "POST /pipelines/pg_quality_checks/retry": {
        status: 409,
        body: { error: { code: "runner_disabled", message: "Retries are disabled." } },
      },
    });
    renderAt("/pipelines");
    await userEvent.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Retries are disabled.")).toBeInTheDocument();
  });

  it("filters by status and offers no retry for Spark pipelines", async () => {
    stubApi({ "/pipelines": { body: pipelines } });
    renderAt("/pipelines");
    await screen.findByRole("link", { name: "ingest_payments" });
    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(1);
    await userEvent.selectOptions(screen.getByLabelText("Last status"), "FAILED");
    expect(screen.queryByRole("link", { name: "pg_quality_checks" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "ingest_payments" })).toBeInTheDocument();
  });
});

describe("routing", () => {
  it("shows a not-found page for unknown routes", async () => {
    stubApi({});
    renderAt("/nope");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});
