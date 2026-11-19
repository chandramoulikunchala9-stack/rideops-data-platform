import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/**
 * Filters live in the URL (?start_date=&end_date=&city=), so a filtered view can be shared
 * or bookmarked, and the browser back button undoes a filter change.
 */
export function useFilters() {
  const [params, setParams] = useSearchParams();

  const set = useCallback(
    (updates: Record<string, string | null>) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          for (const [key, value] of Object.entries(updates)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          return next;
        },
        { replace: false },
      );
    },
    [setParams],
  );

  return {
    startDate: params.get("start_date"),
    endDate: params.get("end_date"),
    city: params.get("city"),
    set,
  };
}

/** Preset ranges relative to the last day that has data. */
export function presetRange(endIso: string, days: number): { start: string; end: string } {
  const end = new Date(`${endIso}T00:00:00Z`);
  const start = new Date(end);
  start.setUTCDate(end.getUTCDate() - (days - 1));
  return { start: start.toISOString().slice(0, 10), end: endIso };
}
