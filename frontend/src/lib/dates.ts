// Calendar-date helpers for the date pickers. A date is an ISO "YYYY-MM-DD" string, the same shape
// the API and the URL use, and every calculation runs in UTC so a date never shifts with the
// viewer's timezone. ISO strings also compare correctly as plain strings.

export type IsoDate = string;

const DAY_MS = 86_400_000;

const toDate = (iso: IsoDate) => new Date(`${iso}T00:00:00Z`);
const toIso = (date: Date): IsoDate => date.toISOString().slice(0, 10);

export function todayIso(now = new Date()): IsoDate {
  return toIso(now);
}

export function addDays(iso: IsoDate, days: number): IsoDate {
  return toIso(new Date(toDate(iso).getTime() + days * DAY_MS));
}

/** Same day of the month, clamped to the target month's length (31 Jan + 1 month = 28/29 Feb). */
export function addMonths(iso: IsoDate, months: number): IsoDate {
  const d = toDate(iso);
  const target = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + months, 1));
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(d.getUTCDate(), lastDay));
  return toIso(target);
}

export const startOfMonth = (iso: IsoDate): IsoDate => `${iso.slice(0, 7)}-01`;

export function endOfMonth(iso: IsoDate): IsoDate {
  const d = toDate(iso);
  return toIso(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)));
}

/** 0 = Monday ... 6 = Sunday (weeks start on Monday, ISO 8601). */
export const weekdayIndex = (iso: IsoDate) => (toDate(iso).getUTCDay() + 6) % 7;

export const dayOfMonth = (iso: IsoDate) => toDate(iso).getUTCDate();

/** Number of days from `start` to `end`, counting both ends. */
export const daysInclusive = (start: IsoDate, end: IsoDate) =>
  Math.round((toDate(end).getTime() - toDate(start).getTime()) / DAY_MS) + 1;

export function clampDate(iso: IsoDate, min?: IsoDate | null, max?: IsoDate | null): IsoDate {
  if (min && iso < min) return min;
  if (max && iso > max) return max;
  return iso;
}

export const isValidIso = (value: string | null | undefined): value is IsoDate =>
  !!value && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(toDate(value).getTime());

/**
 * Six Monday-first weeks covering the month of `iso`. Days outside the month are null, so two
 * months shown side by side never repeat a date, and every month has the same height.
 */
export function monthWeeks(iso: IsoDate): (IsoDate | null)[][] {
  const first = startOfMonth(iso);
  const last = endOfMonth(iso);
  let day = addDays(first, -weekdayIndex(first));
  const weeks: (IsoDate | null)[][] = [];
  for (let w = 0; w < 6; w++) {
    const week: (IsoDate | null)[] = [];
    for (let i = 0; i < 7; i++) {
      week.push(day >= first && day <= last ? day : null);
      day = addDays(day, 1);
    }
    weeks.push(week);
  }
  return weeks;
}

const fmt = (options: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "UTC" });
const fieldFormat = fmt({ day: "2-digit", month: "short", year: "numeric" });
const shortFormat = fmt({ day: "2-digit", month: "short" });
const monthFormat = fmt({ month: "long", year: "numeric" });
const longFormat = fmt({ weekday: "long", day: "numeric", month: "long", year: "numeric" });

/** "02 Oct 2026" */
export const formatFieldDate = (iso: IsoDate) => fieldFormat.format(toDate(iso));
/** "October 2026" */
export const formatMonth = (iso: IsoDate) => monthFormat.format(toDate(iso));
/** "Friday 2 October 2026", for screen readers. */
export const formatLongDate = (iso: IsoDate) => longFormat.format(toDate(iso)).replace(",", "");

/** "02 Oct – 31 Oct 2026", or "28 Dec 2025 – 03 Jan 2026" across a year boundary. */
export function formatRange(start: IsoDate, end: IsoDate): string {
  if (start.slice(0, 4) === end.slice(0, 4)) {
    return `${shortFormat.format(toDate(start))} – ${formatFieldDate(end)}`;
  }
  return `${formatFieldDate(start)} – ${formatFieldDate(end)}`;
}

/** Weekday column heads, Monday first: [["Mo", "Monday"], ...]. */
export const WEEKDAYS: readonly (readonly [string, string])[] = [
  ["Mo", "Monday"],
  ["Tu", "Tuesday"],
  ["We", "Wednesday"],
  ["Th", "Thursday"],
  ["Fr", "Friday"],
  ["Sa", "Saturday"],
  ["Su", "Sunday"],
];
