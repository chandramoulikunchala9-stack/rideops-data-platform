import { CITY_NAMES, CITY_ORDER } from "../lib/charts";
import { presetRange, useFilters } from "../lib/filters";

const PRESETS = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
];

/** Date range + city filters in one row above the charts; state lives in the URL. */
export function DateRangeFilter({ dataEnd, showCity = false }: { dataEnd: string | null; showCity?: boolean }) {
  const { startDate, endDate, city, set } = useFilters();
  const activeDays =
    startDate && endDate
      ? Math.round((Date.parse(endDate) - Date.parse(startDate)) / 86_400_000) + 1
      : 30; // the API default

  return (
    <div className="toolbar" role="group" aria-label="Filters">
      {dataEnd && (
        <div className="segmented" role="group" aria-label="Date range preset">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              aria-pressed={activeDays === p.days && (!endDate || endDate === dataEnd)}
              onClick={() => {
                const range = presetRange(dataEnd, p.days);
                set({ start_date: range.start, end_date: range.end });
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      )}
      <label className="field">
        From
        <input type="date" value={startDate ?? ""} max={endDate ?? dataEnd ?? undefined}
          onChange={(e) => set({ start_date: e.target.value || null })} />
      </label>
      <label className="field">
        To
        <input type="date" value={endDate ?? ""} min={startDate ?? undefined} max={dataEnd ?? undefined}
          onChange={(e) => set({ end_date: e.target.value || null })} />
      </label>
      {showCity && (
        <label className="field">
          City
          <select value={city ?? ""} onChange={(e) => set({ city: e.target.value || null })}>
            <option value="">All cities</option>
            {CITY_ORDER.map((code) => (
              <option key={code} value={code}>
                {CITY_NAMES[code]}
              </option>
            ))}
          </select>
        </label>
      )}
      {(startDate || endDate || city) && (
        <button type="button" className="btn small" onClick={() => set({ start_date: null, end_date: null, city: null })}>
          Reset
        </button>
      )}
    </div>
  );
}

export function Pagination({ total, limit, offset, onChange }: {
  total: number; limit: number; offset: number; onChange: (offset: number) => void;
}) {
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  return (
    <nav className="toolbar" aria-label="Pagination" style={{ justifyContent: "flex-end", marginTop: 10 }}>
      <span style={{ color: "var(--text-muted)", fontSize: 13 }}>
        {total === 0 ? "0 results" : `${offset + 1}–${Math.min(offset + limit, total)} of ${total}`}
      </span>
      <button type="button" className="btn small" disabled={page <= 1} onClick={() => onChange(offset - limit)}>
        Previous
      </button>
      <button type="button" className="btn small" disabled={page >= pages} onClick={() => onChange(offset + limit)}>
        Next
      </button>
    </nav>
  );
}
