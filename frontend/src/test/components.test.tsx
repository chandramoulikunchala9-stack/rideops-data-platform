import { render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useApi } from "../api/useApi";
import { AsyncView } from "../components/AsyncView";
import { ChartCard } from "../components/ChartCard";
import { SourceBadge, StatusBadge } from "../components/StatusBadge";

function mockFetch(responses: Record<string, { status?: number; body: unknown }>) {
  const fetchMock = vi.fn(async (url: string) => {
    const path = url.replace("/api/v1", "");
    const match = responses[path];
    if (!match) return new Response(JSON.stringify({ error: { code: "not_found", message: "nope" } }), { status: 404 });
    return new Response(JSON.stringify(match.body), { status: match.status ?? 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("StatusBadge", () => {
  it("never relies on colour alone: icon + text label", () => {
    render(<StatusBadge status="FAILED" />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("✕")).toBeInTheDocument();
  });

  it("labels simulated data", () => {
    render(<SourceBadge sources={["simulated"]} />);
    expect(screen.getByText("SIMULATED")).toBeInTheDocument();
  });
});

describe("ChartCard", () => {
  it("offers an accessible table view of the same data", async () => {
    render(
      <ChartCard
        title="Revenue"
        legend={[{ label: "Bengaluru", color: "red" }, { label: "Mumbai", color: "blue" }]}
        chart={<div>the chart</div>}
        table={<table><tbody><tr><td>BLR 100</td></tr></tbody></table>}
      />,
    );
    expect(screen.getByText("the chart")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Legend" })).toHaveTextContent("Bengaluru");
    await userEvent.click(screen.getByRole("button", { name: "Table" }));
    expect(screen.getByText("BLR 100")).toBeInTheDocument();
    expect(screen.queryByText("the chart")).not.toBeInTheDocument();
  });
});

describe("useApi + AsyncView", () => {
  it("goes from loading to data", async () => {
    mockFetch({ "/thing": { body: { value: 42 } } });
    const { result } = renderHook(() => useApi<{ value: number }>("/thing"));
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.data).toEqual({ value: 42 }));
    expect(result.current.loading).toBe(false);
  });

  it("shows an error state with a retry button", async () => {
    mockFetch({});
    function Probe() {
      const state = useApi<string>("/missing");
      return <AsyncView state={state}>{(d) => <p>{d}</p>}</AsyncView>;
    }
    render(<Probe />);
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("shows the empty state", async () => {
    mockFetch({ "/list": { body: [] } });
    function Probe() {
      const state = useApi<number[]>("/list");
      return <AsyncView state={state} isEmpty={(d) => d.length === 0} empty="Nothing here">{() => <p>rows</p>}</AsyncView>;
    }
    render(<Probe />);
    expect(await screen.findByText("Nothing here")).toBeInTheDocument();
  });

  it("does not fetch when the path is null", () => {
    const fetchMock = mockFetch({});
    const { result } = renderHook(() => useApi("/x".length > 5 ? "/x" : null));
    expect(result.current.loading).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
