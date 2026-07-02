import type { CalendarDay, DayFacts } from "./insights";
import type { MapPoint } from "./map";

export type JournalDaySummary = {
  mood: MapPoint["mood"];
  mood_confidence: number;
  summary: string;
  key_topics: string[];
  weekday: string;
  is_weekend: boolean;
  season: string;
  facts: DayFacts;
};

export type JournalNote = {
  id: string;
  ts: string;
  kind: "voice" | "text" | null;
  heading_display: string;
  raw_text: string;
  mood: MapPoint["mood"] | null;
  topics: string[];
  gist: string | null;
};

export type JournalDayPayload = {
  date: string;
  prev_date: string | null;
  next_date: string | null;
  day: JournalDaySummary | null;
  notes: JournalNote[];
};

export type JournalMonthDay = {
  date: string;
  note_count: number;
  mood: CalendarDay["mood"] | null;
};

export type JournalMonthPayload = {
  days: JournalMonthDay[];
};

export async function fetchJournalDay(date: string): Promise<JournalDayPayload> {
  return fetchJournal(`/api/days/${date}`);
}

export async function fetchLatestJournalDay(): Promise<JournalDayPayload> {
  return fetchJournal("/api/days/latest");
}

export async function fetchJournalMonth(month: string): Promise<JournalMonthPayload> {
  const response = await fetch(`/api/days?month=${encodeURIComponent(month)}`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error("JOURNAL MONTH UNAVAILABLE");
  }
  return response.json() as Promise<JournalMonthPayload>;
}

async function fetchJournal(url: string): Promise<JournalDayPayload> {
  const response = await fetch(url, {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error("JOURNAL DAY UNAVAILABLE");
  }
  return response.json() as Promise<JournalDayPayload>;
}
