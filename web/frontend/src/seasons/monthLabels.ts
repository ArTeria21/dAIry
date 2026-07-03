const monthLabelNames = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
const dayMs = 24 * 60 * 60 * 1000;

export type MonthFirstInWeek = {
  date: string;
  label: string;
};

export function monthFirstInWeek(weekStart: Date | string): MonthFirstInWeek | null {
  const start = typeof weekStart === "string" ? parseUtcDate(weekStart) : cloneUtcDate(weekStart);
  for (let offset = 0; offset < 7; offset += 1) {
    const cursor = new Date(start.getTime() + offset * dayMs);
    if (cursor.getUTCDate() === 1) {
      return {
        date: isoDate(cursor),
        label: monthLabel(cursor.getUTCMonth()),
      };
    }
  }
  return null;
}

export function monthLabel(monthIndex: number): string {
  return monthLabelNames[monthIndex] ?? "";
}

function parseUtcDate(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

function cloneUtcDate(value: Date): Date {
  return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), value.getUTCDate()));
}

function isoDate(value: Date): string {
  return value.toISOString().slice(0, 10);
}
