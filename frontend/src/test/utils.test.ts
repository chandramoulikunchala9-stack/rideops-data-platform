import { describe, expect, it, vi } from "vitest";

import { api, ApiError, query } from "../api/client";
import { CITY_ORDER, cityColor, pivotByCity } from "../lib/charts";
import { presetRange } from "../lib/filters";
import { formatDuration, formatInr, formatPct, relativeTime } from "../lib/format";

describe("format", () => {
  it("formats durations, percentages and money", () => {
    expect(formatDuration(42)).toBe("42s");
    expect(formatDuration(125)).toBe("2m 5s");
    expect(formatDuration(3720)).toBe("1h 2m");
    expect(formatDuration(null)).toBe("–");
    expect(formatPct(0.1234)).toBe("12.3%");
    expect(formatInr(1500)).toContain("1,500");
  });

  it("describes relative times", () => {
    const now = Date.parse("2026-11-19T12:00:00Z");
    expect(relativeTime("2026-11-19T11:30:00Z", now)).toBe("30 min ago");
    expect(relativeTime("2026-11-17T12:00:00Z", now)).toBe("2 d ago");
    expect(relativeTime(null, now)).toBe("never");
  });
});

describe("charts", () => {
  it("assigns each city a fixed colour slot regardless of which cities are shown", () => {
    expect(CITY_ORDER.map(cityColor)).toEqual([1, 2, 3, 4, 5, 6].map((n) => `var(--series-${n})`));
    expect(cityColor("HYD")).toBe("var(--series-3)"); // same slot whether 1 or 6 cities are visible
    expect(cityColor("XXX")).toBe("var(--status-neutral)");
  });

  it("pivots revenue points into one row per period", () => {
    const rows = pivotByCity([
      { period: "2026-10-01", city: "BLR", revenue_inr: 10 },
      { period: "2026-10-01", city: "MUM", revenue_inr: 20 },
      { period: "2026-10-02", city: "BLR", revenue_inr: 30 },
    ]);
    expect(rows).toEqual([
      { period: "2026-10-01", BLR: 10, MUM: 20 },
      { period: "2026-10-02", BLR: 30 },
    ]);
  });
});

describe("filters", () => {
  it("builds preset ranges ending on the last day of data", () => {
    expect(presetRange("2026-10-31", 7)).toEqual({ start: "2026-10-25", end: "2026-10-31" });
  });
});

describe("api client", () => {
  it("builds query strings without empty values", () => {
    expect(query({ a: 1, b: null, c: "", d: "x" })).toBe("?a=1&d=x");
    expect(query({ a: undefined })).toBe("");
  });

  it("turns the backend error shape into an ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { code: "runner_disabled", message: "Retries are disabled" } }), {
          status: 409,
        }),
      ),
    );
    const error = await api.post("/pipelines/x/retry").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "runner_disabled", message: "Retries are disabled" });
    vi.unstubAllGlobals();
  });

  it("reports network failures clearly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api.get("/health")).rejects.toMatchObject({ code: "network_error" });
    vi.unstubAllGlobals();
  });
});
