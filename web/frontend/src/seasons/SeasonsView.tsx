import { useEffect, useMemo, useState } from "react";

import { moodPalette, type Mood } from "../design/palettes";
import { chromeTextClass, readingTextClass } from "../design/theme";
import {
  fetchCalendar,
  fetchTopicsTimeline,
  topicsByDate,
  type CalendarDay,
  type CalendarPayload,
  type TopicsTimelinePayload,
} from "../services/insights";
import { fetchMap } from "../services/map";
import { cx } from "../ui/classNames";
import { Legend, type LegendItem } from "../ui/Legend";
import { Tag } from "../ui/primitives";
import { CalendarHeatmap } from "./CalendarHeatmap";
import { TopicsStream } from "./TopicsStream";

export function SeasonsView() {
  const [calendar, setCalendar] = useState<CalendarPayload>({ days: [] });
  const [timeline, setTimeline] = useState<TopicsTimelinePayload>({ buckets: [] });
  const [dayTopics, setDayTopics] = useState<Map<string, Set<string>>>(new Map());
  const [selectedDay, setSelectedDay] = useState<CalendarDay | null>(null);
  const [selectedMood, setSelectedMood] = useState<Mood | null>(null);
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

  const moodLegendItems = useMemo(() => buildMoodLegendItems(calendar.days), [calendar.days]);

  if (!loaded) {
    return <p className={chromeTextClass}>LOADING SEASONS</p>;
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <section className="grid gap-5">
        <Legend
          activeKey={selectedMood}
          ariaLabel="MOOD LEGEND"
          items={moodLegendItems}
          onToggle={(key) => setSelectedMood((current) => (current === key ? null : (key as Mood)))}
        />
        <CalendarHeatmap
          days={calendar.days}
          onSelectDay={setSelectedDay}
          selectedDay={selectedDay}
          selectedMood={selectedMood}
          selectedTopic={selectedTopic}
          topicsByDate={dayTopics}
        />

        <TopicsStream
          buckets={timeline.buckets}
          onSelectTopic={(topic) => setSelectedTopic((current) => (current === topic ? "" : topic))}
          selectedTopic={selectedTopic}
        />
      </section>

      <DayPanel day={selectedDay} />
    </div>
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
        <Tag>{day.mood ? day.mood.toUpperCase() : "NO MOOD"}</Tag>
        <span className={cx(chromeTextClass, "text-[10px] text-slate")}>{day.weekday}</span>
      </div>
      <a className={cx(chromeTextClass, "text-[10px] text-schematic-blue")} href={`#journal/${day.date}`}>
        READ THIS DAY
      </a>
      <p className={cx(readingTextClass, "text-sm leading-6")}>{day.summary}</p>
      <div className="flex flex-wrap gap-2">
        {factChips(day.facts).map((chip) => (
          <Tag key={chip}>{chip}</Tag>
        ))}
      </div>
    </aside>
  );
}

function buildMoodLegendItems(days: CalendarDay[]): LegendItem[] {
  const counts = new Map<Mood, number>();
  days.forEach((day) => {
    if (day.mood) {
      counts.set(day.mood, (counts.get(day.mood) ?? 0) + 1);
    }
  });

  return (Object.keys(moodPalette) as Mood[])
    .filter((mood) => counts.has(mood))
    .map((mood) => ({
      key: mood,
      label: mood.toUpperCase(),
      count: counts.get(mood),
      swatch: moodPalette[mood],
      swatchTestId: `mood-swatch-${mood}`,
    }));
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
  if (!Array.isArray(payload.buckets)) {
    return { buckets: [] };
  }
  return {
    buckets: payload.buckets.map((bucket) => ({
      period: bucket.period,
      total: Number(bucket.total) || 0,
      counts: Object.fromEntries(
        Object.entries(bucket.counts ?? {}).map(([topic, count]) => [topic, Number(count) || 0]),
      ),
    })),
  };
}
