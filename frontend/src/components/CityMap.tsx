import { CITY_NAMES, cityColor } from "../lib/charts";
import { formatInrCompact, formatInt } from "../lib/format";

interface CityValue {
  city: string;
  revenue_inr: number;
  bookings: number;
}

// Schematic positions: they keep India's north / west / centre / south arrangement readable without
// pretending to be a surveyed map (Mumbai and Pune would otherwise sit on top of each other).
const POS: Record<string, { x: number; y: number; label: "below" | "right" | "left" }> = {
  DEL: { x: 236, y: 62, label: "right" },
  MUM: { x: 110, y: 150, label: "below" },
  PUN: { x: 172, y: 266, label: "left" },
  HYD: { x: 288, y: 196, label: "below" },
  BLR: { x: 206, y: 340, label: "left" },
  CHE: { x: 318, y: 318, label: "below" },
};

export function CityMap({ cities }: { cities: CityValue[] }) {
  const max = Math.max(...cities.map((c) => c.revenue_inr), 1);
  const radius = (v: number) => 10 + Math.sqrt(v / max) * 26;
  const placed = cities.filter((c) => POS[c.city]);
  const label = placed
    .map((c) => `${CITY_NAMES[c.city] ?? c.city} ${formatInrCompact(c.revenue_inr)}`)
    .join(", ");

  return (
    <svg className="city-map" viewBox="0 0 440 410" role="img" aria-label={`Revenue by city, schematic map: ${label}.`}>
      <defs>
        <pattern id="city-map-dots" width="16" height="16" patternUnits="userSpaceOnUse">
          <circle cx="1.5" cy="1.5" r="1.1" className="city-map-dot" />
        </pattern>
      </defs>
      <rect width="440" height="410" fill="url(#city-map-dots)" />
      {placed.map((c) => {
        const p = POS[c.city]!;
        const r = radius(c.revenue_inr);
        const color = cityColor(c.city);
        const tx = p.label === "right" ? p.x + r + 8 : p.label === "left" ? p.x - r - 8 : p.x;
        const ty = p.label === "below" ? p.y + r + 18 : p.y - 2;
        const anchor = p.label === "right" ? "start" : p.label === "left" ? "end" : "middle";
        return (
          <g key={c.city}>
            <circle cx={p.x} cy={p.y} r={r} fill={color} fillOpacity={0.22} stroke={color} strokeWidth={2} />
            <circle cx={p.x} cy={p.y} r={3} fill={color} />
            <text x={tx} y={ty} textAnchor={anchor} className="city-name">
              {CITY_NAMES[c.city] ?? c.city}
            </text>
            <text x={tx} y={ty + 18} textAnchor={anchor} className="city-value">
              {formatInrCompact(c.revenue_inr)} · {formatInt(c.bookings)} rides
            </text>
          </g>
        );
      })}
    </svg>
  );
}
