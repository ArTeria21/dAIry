import type { MapPayload } from "./map";

export type DayFacts = {
  sleep_quality?: number | null;
  sport?: boolean | null;
  reading?: boolean | null;
  purchases?: boolean | null;
  eating_outside?: boolean | null;
  deep_focus?: boolean | null;
};

export type CalendarDay = {
  date: string;
  weekday: string;
  is_weekend: boolean;
  season: string;
  mood: "joy" | "calm" | "sadness" | "anger" | "fear" | "neutral" | "mixed";
  mood_confidence: number;
  summary: string;
  facts: DayFacts;
};

export type CalendarPayload = {
  days: CalendarDay[];
};

export type TopicBucket = {
  period: string;
  counts: Record<string, number>;
};

export type TopicsTimelinePayload = {
  buckets: TopicBucket[];
};

export type ResurfaceDay = {
  date: string;
  weekday: string;
  mood: CalendarDay["mood"];
  key_topics: string[];
  summary: string;
};

export type ResurfacePayload = {
  day: ResurfaceDay;
};

export async function fetchCalendar(): Promise<CalendarPayload> {
  const response = await fetch("/api/calendar", {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error("CALENDAR UNAVAILABLE");
  }
  return response.json() as Promise<CalendarPayload>;
}

export async function fetchTopicsTimeline(): Promise<TopicsTimelinePayload> {
  const response = await fetch("/api/topics/timeline?bucket=week", {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error("TOPICS UNAVAILABLE");
  }
  return response.json() as Promise<TopicsTimelinePayload>;
}

export async function fetchResurface(): Promise<ResurfacePayload> {
  const response = await fetch("/api/resurface", {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error("MEMORY UNAVAILABLE");
  }
  return response.json() as Promise<ResurfacePayload>;
}

export function topicsByDate(map: MapPayload | null): Map<string, Set<string>> {
  const grouped = new Map<string, Set<string>>();
  for (const point of map?.points ?? []) {
    const topics = grouped.get(point.date) ?? new Set<string>();
    point.topics.forEach((topic) => topics.add(topic));
    grouped.set(point.date, topics);
  }
  return grouped;
}
