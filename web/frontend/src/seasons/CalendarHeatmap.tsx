import type { KeyboardEvent } from "react";

import { moodPalette, type Mood } from "../design/palettes";
import { chromeTextClass } from "../design/theme";
import type { CalendarDay } from "../services/insights";
import { cx } from "../ui/classNames";
import { buildCalendarYearBlocks } from "./calendarLayout";

const cellSize = 12;
const cellGap = 3;
const labelWidth = 34;
const topOffset = 20;
const fogColor = "#eeeeee";
const hairlineColor = "#e5e5e5";
const signalOrange = "#fb631b";

type CalendarHeatmapProps = {
  days: CalendarDay[];
  selectedDay: CalendarDay | null;
  selectedMood: Mood | null;
  selectedTopic: string;
  topicsByDate: Map<string, Set<string>>;
  onSelectDay: (day: CalendarDay) => void;
};

export function CalendarHeatmap({
  days,
  onSelectDay,
  selectedDay,
  selectedMood,
  selectedTopic,
  topicsByDate,
}: CalendarHeatmapProps) {
  const blocks = buildCalendarYearBlocks(days);

  return (
    <section aria-label="MOOD CALENDAR" className="grid gap-4">
      {blocks.length === 0 ? (
        <p className={cx(chromeTextClass, "py-8 text-center text-[11px] text-slate")}>NO DAYS TO SHOW</p>
      ) : (
        blocks.map((block) => {
          const width = labelWidth + block.weekCount * (cellSize + cellGap);
          const height = topOffset + 7 * (cellSize + cellGap);

          return (
            <div className="grid gap-2" key={block.year}>
              <h3 className={cx(chromeTextClass, "text-[10px] text-slate")}>{block.year}</h3>
              <svg
                aria-label={`MOOD CALENDAR ${block.year}`}
                className="max-w-full overflow-visible"
                data-testid={`calendar-year-${block.year}`}
                role="img"
                viewBox={`0 0 ${width} ${height}`}
              >
                {block.monthLabels.map((label) => (
                  <text
                    className={chromeTextClass}
                    fill="#858483"
                    fontSize="9"
                    key={`${label.label}-${label.weekIndex}`}
                    x={labelWidth + label.weekIndex * (cellSize + cellGap)}
                    y="9"
                  >
                    {label.label}
                  </text>
                ))}
                {[0, 2, 4].map((dayIndex) => (
                  <text
                    className={chromeTextClass}
                    fill="#858483"
                    fontSize="9"
                    key={dayIndex}
                    x="0"
                    y={topOffset + dayIndex * (cellSize + cellGap) + cellSize - 2}
                  >
                    {["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][dayIndex]}
                  </text>
                ))}
                {block.cells.map((cell) => (
                  <CalendarCell
                    cellDate={cell.date}
                    day={cell.day}
                    dayIndex={cell.dayIndex}
                    key={`${block.year}-${cell.date}`}
                    muted={isMuted(cell.day, selectedMood, selectedTopic, topicsByDate)}
                    onSelectDay={onSelectDay}
                    selected={cell.day !== null && selectedDay?.date === cell.day.date}
                    weekIndex={cell.weekIndex}
                  />
                ))}
              </svg>
            </div>
          );
        })
      )}
    </section>
  );
}

function CalendarCell({
  cellDate,
  day,
  dayIndex,
  muted,
  onSelectDay,
  selected,
  weekIndex,
}: {
  cellDate: string;
  day: CalendarDay | null;
  dayIndex: number;
  muted: boolean;
  onSelectDay: (day: CalendarDay) => void;
  selected: boolean;
  weekIndex: number;
}) {
  const x = labelWidth + weekIndex * (cellSize + cellGap);
  const y = topOffset + dayIndex * (cellSize + cellGap);
  const fill = day?.mood ? moodPalette[day.mood] : day ? fogColor : "transparent";
  const fillOpacity = day ? (muted ? 0.15 : confidenceOpacity(day.mood_confidence)) : 0;
  const label = day ? `${day.date} · ${day.mood ? day.mood.toUpperCase() : "NO MOOD"}` : `${cellDate} · EMPTY`;

  function select() {
    if (day) {
      onSelectDay(day);
    }
  }

  function onKeyDown(event: KeyboardEvent<SVGRectElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select();
    }
  }

  return (
    <rect
      aria-label={day ? label : undefined}
      data-testid={`calendar-cell-${cellDate}`}
      fill={fill}
      fillOpacity={fillOpacity}
      height={cellSize}
      onClick={select}
      onKeyDown={day ? onKeyDown : undefined}
      role={day ? "button" : undefined}
      rx="2"
      stroke={selected ? signalOrange : hairlineColor}
      strokeWidth={selected ? 2 : 1}
      tabIndex={day ? 0 : undefined}
      width={cellSize}
      x={x}
      y={y}
    >
      <title>{label}</title>
    </rect>
  );
}

function confidenceOpacity(confidence: number | null): number {
  const value = confidence ?? 0.5;
  return 0.35 + 0.65 * Math.min(1, Math.max(0, value));
}

function isMuted(
  day: CalendarDay | null,
  selectedMood: Mood | null,
  selectedTopic: string,
  topicsByDate: Map<string, Set<string>>,
): boolean {
  if (!day) {
    return false;
  }
  const moodMismatch = selectedMood !== null && day.mood !== selectedMood;
  const topicMismatch = selectedTopic !== "" && !topicsByDate.get(day.date)?.has(selectedTopic);
  return moodMismatch || topicMismatch;
}

export const calendarHeatmapMetrics = {
  cellGap,
  cellSize,
};

export const noMoodColor = fogColor;
