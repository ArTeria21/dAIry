import type { CSSProperties } from "react";
import { useEffect, useMemo, useState } from "react";

import { moodPalette, topicHighlightColor, topicMutedColor } from "../design/palettes";
import { chromeTextClass, readingTextClass } from "../design/theme";
import {
  fetchCalendar,
  fetchTopicsTimeline,
  topicsByDate,
  type CalendarDay,
  type CalendarPayload,
  type TopicBucket,
  type TopicsTimelinePayload,
} from "../services/insights";
import { fetchMap } from "../services/map";
import { cx } from "../ui/classNames";
import { Tag } from "../ui/primitives";

const schematicBlue = "#0d6ea5";

type DayStyle = CSSProperties & {
  "--day-color": string;
};

export function SeasonsView() {
  const [calendar, setCalendar] = useState<CalendarPayload>({ days: [] });
  const [timeline, setTimeline] = useState<TopicsTimelinePayload>({ buckets: [] });
  const [dayTopics, setDayTopics] = useState<Map<string, Set<string>>>(new Map());
  const [selectedDay, setSelectedDay] = useState<CalendarDay | null>(null);
  const [selectedTopic, setSelectedTopic] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.allSettled([fetchCalendar(), fetchTopicsTimeline(), fetchMap()]).then((results) => {
      if (!active) {
        return;
      }

      const [calendarResult, timelineResult, mapResult] = results;
      setCalendar(
        calendarResult.status === "fulfilled"
          ? normalizeCalendar(calendarResult.value)
          : { days: [] },
      );
      setTimeline(
        timelineResult.status === "fulfilled"
          ? normalizeTimeline(timelineResult.value)
          : { buckets: [] },
      );
      setDayTopics(mapResult.status === "fulfilled" ? topicsByDate(mapResult.value) : new Map());
      setLoaded(true);
    });
    return () => {
      active = false;
    };
  }, []);

  const topicSeries = useMemo(() => buildTopicSeries(timeline.buckets), [timeline.buckets]);

  if (!loaded) {
    return <p className={chromeTextClass}>LOADING SEASONS</p>;
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <section className="grid gap-5">
        <div
          aria-label="MOOD CALENDAR"
          className="grid min-h-[240px] grid-cols-[repeat(auto-fill,minmax(92px,1fr))] gap-2 rounded-[2px] border border-hairline bg-cream-paper p-3"
        >
          {calendar.days.length === 0 ? (
            <p className={cx(chromeTextClass, "col-span-full self-center text-center text-[11px] text-slate")}>
              NO DAYS TO SHOW
            </p>
          ) : (
            calendar.days.map((day) => (
              <DayCell
                day={day}
                key={day.date}
                onSelect={setSelectedDay}
                selectedTopic={selectedTopic}
                topics={dayTopics.get(day.date)}
              />
            ))
          )}
        </div>

        <section className="grid gap-3 rounded-[2px] border border-hairline bg-cream-paper p-3">
          <div className={cx(chromeTextClass, "text-[10px] text-slate")}>TOPICS OVER TIME</div>
          {topicSeries.length === 0 ? (
            <p className={cx(chromeTextClass, "text-[11px] text-slate")}>NO TOPIC SIGNAL</p>
          ) : (
            topicSeries.map((series) => (
              <TopicSparkline
                key={series.topic}
                onSelect={setSelectedTopic}
                selected={selectedTopic === series.topic}
                series={series}
              />
            ))
          )}
        </section>
      </section>

      <DayPanel day={selectedDay} />
    </div>
  );
}

function DayCell({
  day,
  onSelect,
  selectedTopic,
  topics,
}: {
  day: CalendarDay;
  onSelect: (day: CalendarDay) => void;
  selectedTopic: string;
  topics?: Set<string>;
}) {
  const matchesTopic = !selectedTopic || topics?.has(selectedTopic);
  const color = matchesTopic ? moodPalette[day.mood] : topicMutedColor;
  const style: DayStyle = {
    "--day-color": color,
    backgroundColor: "var(--day-color)",
  };

  return (
    <button
      aria-label={day.date}
      className={cx(
        chromeTextClass,
        "min-h-[58px] rounded-[2px] border border-hairline p-2 text-left text-[10px] transition-opacity",
      )}
      onClick={() => onSelect(day)}
      style={style}
      type="button"
    >
      <span>{day.date}</span>
    </button>
  );
}

function DayPanel({ day }: { day: CalendarDay | null }) {
  if (!day) {
    return (
      <aside className="rounded-[2px] border border-hairline bg-cream-paper p-4">
        <p className={cx(chromeTextClass, "text-[10px] text-slate")}>SELECT A DAY</p>
      </aside>
    );
  }

  return (
    <aside
      aria-label={`DAY ${day.date} DETAILS`}
      className="grid content-start gap-4 rounded-[2px] border border-hairline bg-cream-paper p-4"
      role="complementary"
    >
      <div className="flex items-center justify-between gap-3">
        <Tag>{day.mood.toUpperCase()}</Tag>
        <span className={cx(chromeTextClass, "text-[10px] text-slate")}>{day.weekday}</span>
      </div>
      <p className={cx(readingTextClass, "text-sm leading-6")}>{day.summary}</p>
      <div className="flex flex-wrap gap-2">
        {factChips(day.facts).map((chip) => (
          <Tag key={chip}>{chip}</Tag>
        ))}
      </div>
    </aside>
  );
}

function TopicSparkline({
  onSelect,
  selected,
  series,
}: {
  onSelect: (topic: string) => void;
  selected: boolean;
  series: TopicSeries;
}) {
  const stroke = selected ? topicHighlightColor : schematicBlue;

  return (
    <button
      aria-pressed={selected}
      className="grid grid-cols-[80px_1fr] items-center gap-3 rounded-[2px] border border-hairline bg-transparent p-2 text-left"
      onClick={() => onSelect(series.topic)}
      type="button"
    >
      <span className={cx(chromeTextClass, "font-plexmono text-[10px]")}>
        {series.topic.toUpperCase()}
      </span>
      <svg aria-hidden="true" className="h-8 w-full" viewBox="0 0 120 32">
        <polyline
          data-testid={`topic-sparkline-${series.topic}`}
          fill="none"
          points={sparklinePoints(series.values)}
          stroke={stroke}
          strokeWidth="1"
        />
      </svg>
    </button>
  );
}

type TopicSeries = {
  topic: string;
  values: number[];
};

function buildTopicSeries(buckets: TopicBucket[]): TopicSeries[] {
  const topics = new Set<string>();
  buckets.forEach((bucket) => {
    Object.keys(bucket.counts).forEach((topic) => topics.add(topic));
  });

  return [...topics].sort().map((topic) => ({
    topic,
    values: buckets.map((bucket) => bucket.counts[topic] ?? 0),
  }));
}

function sparklinePoints(values: number[]): string {
  if (values.length === 0) {
    return "";
  }
  const max = Math.max(1, ...values);
  return values
    .map((value, index) => {
      const x = values.length === 1 ? 0 : (index / (values.length - 1)) * 120;
      const y = 28 - (value / max) * 24;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function factChips(facts: CalendarDay["facts"]): string[] {
  const chips: string[] = [];
  if (typeof facts.sleep_quality === "number") {
    chips.push(`SLEEP ${facts.sleep_quality}/5`);
  }
  if (facts.sport === true) {
    chips.push("SPORT YES");
  }
  if (facts.deep_focus === true) {
    chips.push("DEEP FOCUS YES");
  }
  if (facts.reading === true) {
    chips.push("READING YES");
  }
  if (facts.purchases === true) {
    chips.push("PURCHASES YES");
  }
  if (facts.eating_outside === true) {
    chips.push("EATING OUT YES");
  }
  return chips;
}

function normalizeCalendar(payload: CalendarPayload): CalendarPayload {
  return Array.isArray(payload.days) ? payload : { days: [] };
}

function normalizeTimeline(payload: TopicsTimelinePayload): TopicsTimelinePayload {
  return Array.isArray(payload.buckets) ? payload : { buckets: [] };
}
