import { describe, expect, it } from "vitest";

import { buildCalendarYearBlocks, startOfIsoWeek } from "./calendarLayout";
import { monthFirstInWeek } from "./monthLabels";

describe("Sprint 5 calendar layout", () => {
  it("groups dates into Monday-first weeks and positions month labels", () => {
    const [block] = buildCalendarYearBlocks([{ date: "2026-02-13" }]);
    const friday = block.cells.find((cell) => cell.date === "2026-02-13");

    expect(block.year).toBe(2026);
    expect(friday?.dayIndex).toBe(4);
    expect(friday?.weekIndex).toBe(6);
    expect(block.monthLabels.find((label) => label.label === "FEB")?.weekIndex).toBe(4);
  });

  it("renders the January week tail as empty cells in the new year block", () => {
    const [block] = buildCalendarYearBlocks([{ date: "2026-01-01" }]);
    const tail = block.cells.slice(0, 3);
    const januaryFirst = block.cells.find((cell) => cell.date === "2026-01-01");

    expect(tail.map((cell) => cell.date)).toEqual(["2025-12-29", "2025-12-30", "2025-12-31"]);
    expect(tail.every((cell) => !cell.inYear && cell.day === null)).toBe(true);
    expect(januaryFirst?.dayIndex).toBe(3);
    expect(januaryFirst?.day?.date).toBe("2026-01-01");
    expect(block.monthLabels[0]).toEqual({ label: "JAN", weekIndex: 0 });
  });

  it("returns newest year blocks first and includes leap days", () => {
    const blocks = buildCalendarYearBlocks([{ date: "2024-02-29" }, { date: "2025-01-01" }]);
    const leapBlock = blocks.find((block) => block.year === 2024);
    const leapDay = leapBlock?.cells.find((cell) => cell.date === "2024-02-29");

    expect(blocks.map((block) => block.year)).toEqual([2025, 2024]);
    expect(leapDay?.dayIndex).toBe(3);
    expect(leapDay?.day?.date).toBe("2024-02-29");
    expect(leapBlock?.monthLabels.find((label) => label.label === "MAR")?.weekIndex).toBe(8);
  });

  it("finds the Monday start of an ISO week using UTC dates", () => {
    expect(startOfIsoWeek(new Date(Date.UTC(2026, 0, 1))).toISOString().slice(0, 10)).toBe("2025-12-29");
  });

  it("uses the shared month-label rule for weeks containing the first day", () => {
    const [block] = buildCalendarYearBlocks([{ date: "2026-02-13" }]);
    const february = block.monthLabels.find((label) => label.label === "FEB");
    const weekStart = startOfIsoWeek(new Date(Date.UTC(2026, 1, 1)));

    expect(monthFirstInWeek(weekStart)).toEqual({ date: "2026-02-01", label: "FEB" });
    expect(february?.weekIndex).toBe(4);
  });
});
