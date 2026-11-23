import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DatePicker, DateRangePicker } from "../components/DatePicker";
import { addMonths, daysInclusive, formatRange, monthWeeks } from "../lib/dates";

const day = (name: RegExp) => screen.getByRole("button", { name });

describe("date helpers", () => {
  it("clamps month arithmetic to the target month's length", () => {
    expect(addMonths("2026-01-31", 1)).toBe("2026-02-28");
    expect(addMonths("2026-03-15", -12)).toBe("2025-03-15");
  });

  it("lays out six Monday-first weeks with blanks outside the month", () => {
    const weeks = monthWeeks("2026-10-15");
    expect(weeks).toHaveLength(6);
    // 1 October 2026 is a Thursday: three blanks, then the 1st.
    expect(weeks[0]).toEqual([null, null, null, "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]);
    expect(weeks.flat().filter(Boolean)).toHaveLength(31);
  });

  it("formats ranges and counts days inclusively", () => {
    expect(formatRange("2026-10-02", "2026-10-31")).toBe("02 Oct – 31 Oct 2026");
    expect(formatRange("2025-12-28", "2026-01-03")).toBe("28 Dec 2025 – 03 Jan 2026");
    expect(daysInclusive("2026-10-02", "2026-10-31")).toBe(30);
  });
});

describe("DateRangePicker", () => {
  const setup = (onChange = vi.fn()) => {
    render(
      <DateRangePicker value={{ start: "2026-10-02", end: "2026-10-31" }} max="2026-10-31" onChange={onChange} />,
    );
    return { onChange, user: userEvent.setup() };
  };

  it("shows the range in one field and opens a calendar on the current month", async () => {
    const { user } = setup();
    const trigger = screen.getByRole("button", { name: "Date range 02 Oct – 31 Oct 2026" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Date range" });
    expect(within(dialog).getByText("October 2026")).toBeInTheDocument();
    expect(within(dialog).getByText("· 30 days")).toBeInTheDocument();
    // The end of the range holds focus, ready for the keyboard.
    expect(day(/Saturday 31 October 2026/)).toHaveFocus();
  });

  it("picks a start then an end, in either order, and closes", async () => {
    const { user, onChange } = setup();
    await user.click(screen.getByRole("button", { name: /Date range/ }));

    await user.click(day(/Thursday 22 October 2026/));
    expect(screen.getByText("Now pick the end date")).toBeInTheDocument();
    await user.click(day(/Monday 12 October 2026/));

    expect(onChange).toHaveBeenCalledWith({ start: "2026-10-12", end: "2026-10-22" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Date range/ })).toHaveFocus();
  });

  it("disables days after the last day with data", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: /Date range/ }));
    expect(screen.getByRole("button", { name: "Next month" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Previous month" }));
    expect(screen.getByText("September 2026")).toBeInTheDocument();
  });

  it("moves through days with the arrow keys and closes on Escape", async () => {
    const { user, onChange } = setup();
    await user.click(screen.getByRole("button", { name: /Date range/ }));

    await user.keyboard("{ArrowUp}{ArrowLeft}"); // 31 Oct -> 24 Oct -> 23 Oct
    expect(day(/Friday 23 October 2026/)).toHaveFocus();
    await user.keyboard("{PageUp}"); // -> 23 Sep, the calendar follows
    expect(screen.getByText("September 2026")).toBeInTheDocument();
    expect(day(/Wednesday 23 September 2026/)).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("closes when clicking outside", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: /Date range/ }));
    await user.click(document.body);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("DatePicker", () => {
  it("selects a single date with the keyboard", async () => {
    const onChange = vi.fn();
    render(<DatePicker label="Run date" value="2026-10-15" min="2026-10-01" onChange={onChange} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Run date 15 Oct 2026" }));
    expect(day(/Thursday 15 October 2026/)).toHaveFocus();
    await user.keyboard("{ArrowRight}{Enter}");

    expect(onChange).toHaveBeenCalledWith("2026-10-16");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not let the keyboard leave the allowed dates", async () => {
    render(<DatePicker label="Run date" value="2026-10-02" min="2026-10-01" onChange={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Run date/ }));
    await user.keyboard("{ArrowUp}");
    expect(day(/Thursday 1 October 2026/)).toHaveFocus();
    expect(screen.getByRole("button", { name: "Previous month" })).toBeDisabled();
  });
});
