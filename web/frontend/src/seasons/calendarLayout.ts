const dayMs = 24 * 60 * 60 * 1000;

export type CalendarLayoutDay = {
  date: string;
};

export type CalendarLayoutCell<TDay extends CalendarLayoutDay> = {
  date: string;
  day: TDay | null;
  dayIndex: number;
  inYear: boolean;
  weekIndex: number;
};

export type CalendarMonthLabel = {
  label: string;
  weekIndex: number;
};

export type CalendarYearBlock<TDay extends CalendarLayoutDay> = {
  year: number;
  cells: CalendarLayoutCell<TDay>[];
  monthLabels: CalendarMonthLabel[];
  weekCount: number;
};

const monthLabels = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

export function buildCalendarYearBlocks<TDay extends CalendarLayoutDay>(
  days: TDay[],
): CalendarYearBlock<TDay>[] {
  const byDate = new Map(days.map((day) => [day.date, day]));
  const years = [...new Set(days.map((day) => parseUtcDate(day.date).getUTCFullYear()))].sort(
    (left, right) => right - left,
  );

  return years.map((year) => buildYearBlock(year, byDate));
}

export function startOfIsoWeek(date: Date): Date {
  const copy = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const mondayOffset = (copy.getUTCDay() + 6) % 7;
  copy.setUTCDate(copy.getUTCDate() - mondayOffset);
  return copy;
}

export function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function buildYearBlock<TDay extends CalendarLayoutDay>(
  year: number,
  byDate: Map<string, TDay>,
): CalendarYearBlock<TDay> {
  const start = startOfIsoWeek(new Date(Date.UTC(year, 0, 1)));
  const end = startOfIsoWeek(new Date(Date.UTC(year, 11, 31)));
  end.setUTCDate(end.getUTCDate() + 6);

  const cells: CalendarLayoutCell<TDay>[] = [];
  for (let cursor = new Date(start); cursor <= end; cursor.setUTCDate(cursor.getUTCDate() + 1)) {
    const date = isoDate(cursor);
    const inYear = cursor.getUTCFullYear() === year;
    const offset = Math.floor((cursor.getTime() - start.getTime()) / dayMs);
    cells.push({
      date,
      day: inYear ? (byDate.get(date) ?? null) : null,
      dayIndex: offset % 7,
      inYear,
      weekIndex: Math.floor(offset / 7),
    });
  }

  return {
    year,
    cells,
    monthLabels: monthLabels.map((label, month) => ({
      label,
      weekIndex: Math.floor(
        (startOfIsoWeek(new Date(Date.UTC(year, month, 1))).getTime() - start.getTime()) / (7 * dayMs),
      ),
    })),
    weekCount: Math.ceil(cells.length / 7),
  };
}

function parseUtcDate(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}
