// The dashboard's date pickers: one calendar, two fields.
//   <DatePicker>       a single date
//   <DateRangePicker>  a start and an end in one field (click the start, then the end)
// Dates are ISO "YYYY-MM-DD" strings (see lib/dates.ts), so values go straight into the URL and the API.

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  WEEKDAYS,
  addDays,
  addMonths,
  clampDate,
  dayOfMonth,
  daysInclusive,
  endOfMonth,
  formatFieldDate,
  formatLongDate,
  formatMonth,
  formatRange,
  monthWeeks,
  startOfMonth,
  todayIso,
  weekdayIndex,
  type IsoDate,
} from "../lib/dates";
import { Icon } from "./Icon";

export interface DateRange {
  start: IsoDate;
  end: IsoDate;
}

/* ------------------------------------------------------------------ calendar */

interface CalendarProps {
  /** The day that holds keyboard focus when the calendar opens. */
  initialFocus: IsoDate;
  months: 1 | 2;
  min?: IsoDate | null;
  max?: IsoDate | null;
  /** Days drawn filled (the chosen date, or both ends of a range). */
  selected: IsoDate[];
  /** An inclusive range drawn as a band behind the days. */
  band?: DateRange | null;
  onSelect: (day: IsoDate) => void;
  /** The day under the pointer or keyboard focus, for range previews. */
  onPreview?: (day: IsoDate | null) => void;
  /** Extra words for a day's accessible name, such as "start of range". */
  describe?: (day: IsoDate) => string | undefined;
}

export function Calendar({ initialFocus, months, min, max, selected, band, onSelect, onPreview, describe }: CalendarProps) {
  const today = todayIso();
  const titleId = useId();
  const [focused, setFocused] = useState(() => clampDate(initialFocus, min, max));
  const [view, setView] = useState(() => {
    const first = startOfMonth(clampDate(initialFocus, min, max));
    // When the focused day sits in the last month that has data, show the month before it
    // beside it rather than an empty month after it.
    return months === 2 && max && startOfMonth(max) === first ? addMonths(first, -1) : first;
  });
  const gridRef = useRef<HTMLDivElement>(null);
  const focusDay = useRef(true); // move DOM focus to the focused day after the next render

  useEffect(() => {
    if (!focusDay.current) return;
    focusDay.current = false;
    gridRef.current?.querySelector<HTMLButtonElement>(`[data-date="${focused}"]`)?.focus();
  }, [focused, view]);

  const lastVisible = endOfMonth(addMonths(view, months - 1));
  const prevDisabled = !!min && endOfMonth(addMonths(view, -1)) < min;
  const nextDisabled = !!max && addMonths(view, months) > max;

  const moveTo = (day: IsoDate) => {
    const next = clampDate(day, min, max);
    focusDay.current = true;
    setFocused(next);
    if (next < view) setView(startOfMonth(next));
    else if (next > lastVisible) setView(startOfMonth(addMonths(next, -(months - 1))));
  };

  const shiftView = (delta: number) => {
    setView(addMonths(view, delta));
    setFocused((f) => clampDate(addMonths(f, delta), min, max));
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const day = (event.target as HTMLElement).dataset.date;
    if (!day) return;
    const step: Record<string, () => IsoDate> = {
      ArrowLeft: () => addDays(day, -1),
      ArrowRight: () => addDays(day, 1),
      ArrowUp: () => addDays(day, -7),
      ArrowDown: () => addDays(day, 7),
      Home: () => addDays(day, -weekdayIndex(day)),
      End: () => addDays(day, 6 - weekdayIndex(day)),
      PageUp: () => addMonths(day, event.shiftKey ? -12 : -1),
      PageDown: () => addMonths(day, event.shiftKey ? 12 : 1),
    };
    const next = step[event.key];
    if (!next) return;
    event.preventDefault();
    moveTo(next());
  };

  const navButton = (direction: -1 | 1) => (
    <button
      type="button"
      className="cal-nav"
      aria-label={direction < 0 ? "Previous month" : "Next month"}
      disabled={direction < 0 ? prevDisabled : nextDisabled}
      onClick={() => shiftView(direction)}
    >
      <Icon name={direction < 0 ? "chevronLeft" : "chevronRight"} size={16} />
    </button>
  );

  return (
    <div className="cal-months" ref={gridRef} onKeyDown={onKeyDown} onMouseLeave={() => onPreview?.(null)}>
      {Array.from({ length: months }, (_, i) => {
        const month = addMonths(view, i);
        const monthEnd = endOfMonth(month);
        return (
          <div className="cal-month" key={month}>
            <div className="cal-head">
              {i === 0 ? navButton(-1) : <span className="cal-nav-space" />}
              <div className="cal-title" id={`${titleId}-${i}`} aria-live={i === 0 ? "polite" : undefined}>
                {formatMonth(month)}
              </div>
              {i === months - 1 ? navButton(1) : <span className="cal-nav-space" />}
            </div>
            <table role="grid" className="cal-grid" aria-labelledby={`${titleId}-${i}`}>
              <thead>
                <tr>
                  {WEEKDAYS.map(([short, long]) => (
                    <th key={short} scope="col" abbr={long}>
                      {short}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {monthWeeks(month).map((week, w) => (
                  <tr key={w}>
                    {week.map((day, col) => {
                      if (!day) return <td key={col} className="cal-cell" />;
                      const inBand = !!band && day >= band.start && day <= band.end;
                      const spans = !!band && band.start !== band.end;
                      const cell = ["cal-cell"];
                      if (inBand && spans) {
                        cell.push("in-band");
                        if (day === band.start) cell.push("band-start");
                        else if (col === 0 || dayOfMonth(day) === 1) cell.push("band-l");
                        if (day === band.end) cell.push("band-end");
                        else if (col === 6 || day === monthEnd) cell.push("band-r");
                      }
                      const isSelected = selected.includes(day);
                      const extra = describe?.(day);
                      const name = [formatLongDate(day), day === today ? "today" : undefined, extra]
                        .filter(Boolean)
                        .join(", ");
                      return (
                        <td key={col} className={cell.join(" ")} aria-selected={isSelected}>
                          <button
                            type="button"
                            className="cal-day"
                            data-date={day}
                            tabIndex={day === focused ? 0 : -1}
                            disabled={(!!min && day < min) || (!!max && day > max)}
                            aria-label={name}
                            aria-current={day === today ? "date" : undefined}
                            onClick={() => onSelect(day)}
                            onMouseEnter={() => onPreview?.(day)}
                            onFocus={() => {
                              setFocused(day);
                              onPreview?.(day);
                            }}
                          >
                            {dayOfMonth(day)}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ field + popover */

interface PickerShellProps {
  label: string;
  display: string | null;
  placeholder: string;
  /** Show two months side by side when the viewport has room. */
  wide?: boolean;
  renderPopover: (close: () => void, months: 1 | 2) => ReactNode;
}

function PickerShell({ label, display, placeholder, wide = false, renderPopover }: PickerShellProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [months, setMonths] = useState<1 | 2>(1);
  const [align, setAlign] = useState<"start" | "end">("start");
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => setOpen(false), []);

  // Closing by choosing a date or pressing Escape removes the focused day from the page; give focus
  // back to the field. A click outside or a Tab away already put focus somewhere the user chose.
  const wasOpen = useRef(false);
  const dismissedOutside = useRef(false);
  useEffect(() => {
    const active = document.activeElement;
    if (wasOpen.current && !open && !dismissedOutside.current && (!active || active === document.body)) {
      triggerRef.current?.focus();
    }
    wasOpen.current = open;
    dismissedOutside.current = false;
  }, [open]);

  const toggle = () => {
    if (open) return setOpen(false);
    const roomy = typeof window.matchMedia === "function" && window.matchMedia("(min-width: 720px)").matches;
    setMonths(wide && roomy ? 2 : 1);
    setOpen(true);
  };

  // A click anywhere outside the field closes the popover.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current?.contains(event.target as Node)) return;
      dismissedOutside.current = true;
      setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  // Open toward whichever side keeps the popover on screen (the filters sit at the right edge).
  useLayoutEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current?.getBoundingClientRect();
    const width = popoverRef.current?.offsetWidth ?? 0;
    if (trigger) setAlign(trigger.left + width > window.innerWidth - 16 ? "end" : "start");
  }, [open, months]);

  return (
    <div
      className="field date-field"
      ref={rootRef}
      onBlur={(event) => {
        // Tabbing out of the popover closes it; clicks inside it keep it open.
        const next = event.relatedTarget as Node | null;
        if (open && next && !rootRef.current?.contains(next)) setOpen(false);
      }}
    >
      <label htmlFor={`${id}-trigger`} id={`${id}-label`}>
        {label}
      </label>
      <button
        ref={triggerRef}
        type="button"
        id={`${id}-trigger`}
        className="date-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-labelledby={`${id}-label ${id}-value`}
        onClick={toggle}
      >
        <span id={`${id}-value`} className={display ? "date-value" : "date-value is-empty"}>
          {display ?? placeholder}
        </span>
        <Icon name="calendar" size={16} />
      </button>
      {open && (
        <div
          ref={popoverRef}
          role="dialog"
          aria-labelledby={`${id}-label`}
          className="date-popover"
          data-align={align}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.stopPropagation();
            close();
          }}
        >
          {renderPopover(close, months)}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ single date */

interface DatePickerProps {
  label: string;
  value: IsoDate | null;
  onChange: (day: IsoDate) => void;
  min?: IsoDate | null;
  max?: IsoDate | null;
  placeholder?: string;
}

export function DatePicker({ label, value, onChange, min, max, placeholder = "Pick a date" }: DatePickerProps) {
  return (
    <PickerShell
      label={label}
      display={value ? formatFieldDate(value) : null}
      placeholder={placeholder}
      renderPopover={(close) => (
        <Calendar
          months={1}
          initialFocus={value ?? max ?? todayIso()}
          min={min}
          max={max}
          selected={value ? [value] : []}
          onSelect={(day) => {
            onChange(day);
            close();
          }}
        />
      )}
    />
  );
}

/* ------------------------------------------------------------------ date range */

interface DateRangePickerProps {
  label?: string;
  value: { start: IsoDate | null; end: IsoDate | null };
  onChange: (range: DateRange) => void;
  min?: IsoDate | null;
  max?: IsoDate | null;
  placeholder?: string;
}

export function DateRangePicker({ label = "Date range", value, onChange, min, max, placeholder = "Any dates" }: DateRangePickerProps) {
  const { start, end } = value;
  const display =
    start && end ? formatRange(start, end) : start ? `From ${formatFieldDate(start)}` : end ? `Until ${formatFieldDate(end)}` : null;

  return (
    <PickerShell
      label={label}
      display={display}
      placeholder={placeholder}
      wide
      renderPopover={(close, months) => (
        <RangeCalendar
          value={value}
          min={min}
          max={max}
          months={months}
          onCommit={(range) => {
            onChange(range);
            close();
          }}
        />
      )}
    />
  );
}

const ordered = (a: IsoDate, b: IsoDate): DateRange => (a <= b ? { start: a, end: b } : { start: b, end: a });

function RangeCalendar({ value, min, max, months, onCommit }: {
  value: { start: IsoDate | null; end: IsoDate | null };
  min?: IsoDate | null;
  max?: IsoDate | null;
  months: 1 | 2;
  onCommit: (range: DateRange) => void;
}) {
  // The first click fixes one end (the anchor); the day under the pointer previews the other.
  const [anchor, setAnchor] = useState<IsoDate | null>(null);
  const [preview, setPreview] = useState<IsoDate | null>(null);

  const band: DateRange | null = anchor
    ? ordered(anchor, preview ?? anchor)
    : value.start && value.end
      ? { start: value.start, end: value.end }
      : null;
  const selected = band ? [band.start, band.end] : [value.start, value.end].filter((d): d is IsoDate => !!d);
  const days = band ? daysInclusive(band.start, band.end) : 0;

  return (
    <>
      <Calendar
        months={months}
        initialFocus={value.end ?? value.start ?? max ?? todayIso()}
        min={min}
        max={max}
        selected={selected}
        band={band}
        onPreview={setPreview}
        describe={(day) => {
          if (anchor) return day === anchor ? "range start" : undefined;
          if (!band) return undefined;
          if (day === band.start) return "start of selected range";
          if (day === band.end) return "end of selected range";
          return undefined;
        }}
        onSelect={(day) => {
          if (!anchor) {
            setAnchor(day);
            setPreview(day);
          } else {
            onCommit(ordered(anchor, day));
          }
        }}
      />
      <div className="date-popover-foot">
        <span className="date-summary">
          {band ? (
            <>
              {formatRange(band.start, band.end)}
              <span className="date-days"> · {days === 1 ? "1 day" : `${days} days`}</span>
            </>
          ) : (
            "No range selected"
          )}
        </span>
        <span className="date-hint" aria-live="polite">
          {anchor ? "Now pick the end date" : "Pick a start date"}
        </span>
      </div>
    </>
  );
}
