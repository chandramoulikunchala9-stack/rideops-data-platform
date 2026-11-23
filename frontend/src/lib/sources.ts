import type { IconName } from "../components/Icon";
import type { SourceApp } from "../api/types";

// Each source application keeps one colour and one icon everywhere it appears.
export const SOURCE_META: Record<SourceApp["id"], { color: string; icon: IconName }> = {
  rider_app: { color: "var(--src-rider)", icon: "rider" },
  driver_app: { color: "var(--src-driver)", icon: "driver" },
  payments: { color: "var(--src-payments)", icon: "payments" },
  cloud_billing: { color: "var(--src-billing)", icon: "cloud" },
  platform_ops: { color: "var(--src-platform)", icon: "activity" },
};

export const sourceColor = (id: string) => SOURCE_META[id as SourceApp["id"]]?.color ?? "var(--status-neutral)";
export const sourceIcon = (id: string): IconName => SOURCE_META[id as SourceApp["id"]]?.icon ?? "sources";

/** Defensive read: tests and older APIs may answer with something that is not a Sources body. */
export const sourceList = (body: unknown): SourceApp[] =>
  body && typeof body === "object" && Array.isArray((body as { sources?: unknown }).sources)
    ? (body as { sources: SourceApp[] }).sources
    : [];

/** Rows delivered more than once and merged away: received = kept + quarantined + duplicates. */
export const duplicateRows = (app: SourceApp) =>
  app.entities.reduce((n, e) => n + Math.max(0, e.rows_loaded - e.rows_current - e.rows_rejected), 0);
