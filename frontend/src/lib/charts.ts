// Chart conventions (from the data-viz method): colour follows the ENTITY, never its rank,
// and categorical slots are assigned in a fixed order.

export const CITY_ORDER = ["BLR", "MUM", "HYD", "DEL", "CHE", "PUN"] as const;

export const CITY_NAMES: Record<string, string> = {
  BLR: "Bengaluru",
  MUM: "Mumbai",
  HYD: "Hyderabad",
  DEL: "Delhi",
  CHE: "Chennai",
  PUN: "Pune",
};

/** City -> series slot. Fixed, so filtering to two cities never repaints them. */
export function cityColor(city: string): string {
  const index = CITY_ORDER.indexOf(city as (typeof CITY_ORDER)[number]);
  return index >= 0 ? `var(--series-${index + 1})` : "var(--status-neutral)";
}

export const seriesColor = (slot: number) => `var(--series-${slot})`;

// Shared Recharts styling: recessive hairline grid, muted axis text, no axis lines.
export const axisProps = {
  stroke: "var(--axis)",
  tick: { fill: "var(--text-muted)", fontSize: 12 },
  tickLine: false,
  axisLine: false,
} as const;

export const gridProps = { stroke: "var(--grid)", strokeDasharray: undefined, vertical: false } as const;

// Bars: ≤ 24px thick, 4px rounded data-end, square at the baseline.
export const BAR_SIZE = 24;
export const BAR_RADIUS: [number, number, number, number] = [4, 4, 0, 0];
export const HBAR_RADIUS: [number, number, number, number] = [0, 4, 4, 0];

/** [{period, city, revenue_inr}] -> [{period, BLR: n, MUM: n, ...}]: one row per x value for a line chart. */
export function pivotByCity(series: { period: string; city: string; revenue_inr: number }[]) {
  const rows = new Map<string, Record<string, number | string>>();
  for (const point of series) {
    const row = rows.get(point.period) ?? { period: point.period };
    row[point.city] = point.revenue_inr;
    rows.set(point.period, row);
  }
  return [...rows.values()];
}
