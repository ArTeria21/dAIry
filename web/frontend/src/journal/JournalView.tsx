import type { CSSProperties } from "react";
import { useEffect, useState } from "react";

import { moodPalette, topicMutedColor } from "../design/palettes";
import { chromeTextClass, readingTextClass } from "../design/theme";
import {
  fetchJournalDay,
  fetchJournalMonth,
  fetchLatestJournalDay,
  type JournalDayPayload,
  type JournalMonthDay,
} from "../services/journal";
import { cx } from "../ui/classNames";
import { Tag } from "../ui/primitives";
import { NoteEditor } from "./NoteEditor";

type MoodDotStyle = CSSProperties & {
  "--mood-color": string;
};

export function JournalView({ date }: { date?: string }) {
  const [payload, setPayload] = useState<JournalDayPayload | null>(null);
  const [month, setMonth] = useState("");
  const [monthDays, setMonthDays] = useState<JournalMonthDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setUnavailable(false);
    const request = date ? fetchJournalDay(date) : fetchLatestJournalDay();

    request
      .then((nextPayload) => {
        if (!active) {
          return;
        }
        setPayload(nextPayload);
        setMonth(nextPayload.date.slice(0, 7));
      })
      .catch(() => {
        if (active) {
          setPayload(null);
          setUnavailable(true);
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [date, reloadKey]);

  useEffect(() => {
    if (!month) {
      return;
    }
    let active = true;
    fetchJournalMonth(month)
      .then((index) => {
        if (active) {
          setMonthDays(index.days);
        }
      })
      .catch(() => {
        if (active) {
          setMonthDays([]);
        }
      });
    return () => {
      active = false;
    };
  }, [month]);

  if (loading && !payload) {
    return <p className={chromeTextClass}>LOADING JOURNAL</p>;
  }

  if (unavailable) {
    return (
      <section className="rounded-[2px] border-t border-hairline bg-cream-paper py-5">
        <p className={cx(chromeTextClass, "text-[11px] text-slate")}>
          {date ? "DAY UNAVAILABLE" : "NO ENTRIES YET"}
        </p>
      </section>
    );
  }

  if (!payload) {
    return null;
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
      <article aria-label={`JOURNAL DAY ${payload.date}`} className="grid content-start gap-6">
        <header className="grid gap-3 border-b border-hairline pb-5">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className={cx(readingTextClass, "text-3xl font-medium leading-[1.11]")}>{payload.date}</h3>
            {payload.day ? <Tag>{payload.day.mood.toUpperCase()}</Tag> : null}
            {payload.day ? (
              <span className={cx(chromeTextClass, "text-[10px] text-slate")}>{payload.day.weekday}</span>
            ) : null}
          </div>
          {payload.day?.summary ? (
            <p className={cx(readingTextClass, "max-w-3xl text-base leading-7")}>{payload.day.summary}</p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {payload.day?.key_topics.map((topic) => (
              <Tag key={topic}>{topic.toUpperCase()}</Tag>
            ))}
          </div>
          <DayNavigation payload={payload} />
        </header>

        {payload.notes.length === 0 ? (
          <p className={cx(chromeTextClass, "text-[11px] text-slate")}>NO NOTES IN THIS DAY</p>
        ) : (
          payload.notes.map((note) => (
            <section className="grid gap-3 border-b border-hairline pb-5" key={note.id}>
              <h4 className={cx(chromeTextClass, "text-[10px] text-slate")}>{noteHeading(note)}</h4>
              <p className={cx(readingTextClass, "whitespace-pre-wrap text-[15px] leading-7")}>{note.raw_text}</p>
              <NoteEditor
                noteId={note.id}
                onReload={() => {
                  setReloadKey((current) => current + 1);
                  return Promise.resolve();
                }}
                rawText={note.raw_text}
                rawTextSha256={note.raw_text_sha256}
              />
              {note.mood || note.topics.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {note.mood ? <Tag>{note.mood.toUpperCase()}</Tag> : null}
                  {note.topics.map((topic) => (
                    <Tag key={topic}>{topic.toUpperCase()}</Tag>
                  ))}
                </div>
              ) : null}
            </section>
          ))
        )}
      </article>

      <MonthIndex currentDate={payload.date} days={monthDays} month={month} onMonthChange={setMonth} />
    </div>
  );
}

function DayNavigation({ payload }: { payload: JournalDayPayload }) {
  return (
    <div className="flex flex-wrap gap-2">
      <button
        className={cx(chromeTextClass, "rounded-[2px] border border-hairline px-3 py-2 text-[10px] text-slate")}
        disabled={!payload.prev_date}
        onClick={() => navigateToDate(payload.prev_date)}
        type="button"
      >
        PREV DAY
      </button>
      <button
        className={cx(chromeTextClass, "rounded-[2px] border border-hairline px-3 py-2 text-[10px] text-slate")}
        disabled={!payload.next_date}
        onClick={() => navigateToDate(payload.next_date)}
        type="button"
      >
        NEXT DAY
      </button>
    </div>
  );
}

function MonthIndex({
  currentDate,
  days,
  month,
  onMonthChange,
}: {
  currentDate: string;
  days: JournalMonthDay[];
  month: string;
  onMonthChange: (month: string) => void;
}) {
  return (
    <aside
      aria-label="JOURNAL MONTH INDEX"
      className="grid content-start gap-3 rounded-[2px] border border-hairline bg-cream-paper p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <button
          className={cx(chromeTextClass, "rounded-[2px] border border-hairline px-2 py-1 text-[10px] text-slate")}
          onClick={() => onMonthChange(shiftMonth(month, -1))}
          type="button"
        >
          PREV
        </button>
        <span className={cx(chromeTextClass, "text-[10px] text-slate")}>{month}</span>
        <button
          className={cx(chromeTextClass, "rounded-[2px] border border-hairline px-2 py-1 text-[10px] text-slate")}
          onClick={() => onMonthChange(shiftMonth(month, 1))}
          type="button"
        >
          NEXT
        </button>
      </div>
      <div className="grid gap-1">
        {days.length === 0 ? (
          <p className={cx(chromeTextClass, "text-[10px] text-slate")}>NO DAYS</p>
        ) : (
          days.map((day) => (
            <button
              aria-pressed={day.date === currentDate}
              className={cx(
                chromeTextClass,
                "grid grid-cols-[10px_1fr_auto] items-center gap-2 rounded-[2px] border px-2 py-2 text-left text-[10px]",
                day.date === currentDate ? "border-schematic-blue text-ink-black" : "border-hairline text-slate",
              )}
              key={day.date}
              onClick={() => navigateToDate(day.date)}
              type="button"
            >
              <MoodDot mood={day.mood} />
              <span>{day.date}</span>
              <span>{day.note_count}</span>
            </button>
          ))
        )}
      </div>
    </aside>
  );
}

function MoodDot({ mood }: { mood: JournalMonthDay["mood"] }) {
  const style: MoodDotStyle = {
    "--mood-color": mood ? moodPalette[mood] : topicMutedColor,
    backgroundColor: "var(--mood-color)",
  };

  return <span aria-hidden="true" className="h-2 w-2 rounded-[2px]" style={style} />;
}

function navigateToDate(date: string | null) {
  if (date) {
    window.location.hash = `#journal/${date}`;
  }
}

function noteHeading(note: JournalDayPayload["notes"][number]): string {
  return note.kind ? `${note.ts} · ${note.kind.toUpperCase()}` : note.heading_display;
}

function shiftMonth(month: string, delta: number): string {
  const [year, monthNumber] = month.split("-").map(Number);
  const date = new Date(Date.UTC(year, monthNumber - 1 + delta, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}
