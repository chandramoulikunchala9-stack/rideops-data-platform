// Formatting helpers: one place decides how numbers, money, dates and durations look.

// Lens is used anywhere, so counts group in thousands (120,331). Rupee amounts on the demo
// warehouse reports keep Indian grouping (₹1.2L), which is how that data is read.
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const compactInr = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat("en-US");

export const formatInt = (n: number | null | undefined) => (n == null ? "–" : integer.format(n));

export const formatCompact = (n: number | null | undefined) => (n == null ? "–" : compact.format(n));

export const formatInr = (n: number | null | undefined, digits = 0) =>
  n == null
    ? "–"
    : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: digits }).format(n);

export const formatInrCompact = (n: number | null | undefined) => (n == null ? "–" : `₹${compactInr.format(n)}`);

export const formatUsd = (n: number | null | undefined, digits = 2) =>
  n == null
    ? "–"
    : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: digits }).format(n);

export const formatPct = (ratio: number | null | undefined, digits = 1) =>
  ratio == null ? "–" : `${(ratio * 100).toFixed(digits)}%`;

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "–";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export const formatDateTime = (iso: string | null | undefined) =>
  iso
    ? new Date(iso).toLocaleString("en-GB", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
        timeZoneName: "short",
      })
    : "–";

export const formatDate = (iso: string | null | undefined) =>
  iso ? new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }) : "–";

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const diff = (now - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return `${Math.floor(diff / 86400)} d ago`;
}

export const humanize = (snake: string) => snake.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
