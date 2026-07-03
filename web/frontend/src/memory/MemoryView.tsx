import { useEffect, useState } from "react";

import { chromeTextClass, readingTextClass } from "../design/theme";
import { fetchResurface, type ResurfaceDay } from "../services/insights";
import { cx } from "../ui/classNames";
import { Button, Tag } from "../ui/primitives";

export function MemoryView() {
  const [day, setDay] = useState<ResurfaceDay | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);

  async function loadMemory() {
    setLoading(true);
    setUnavailable(false);
    try {
      setDay((await fetchResurface()).day);
    } catch {
      setDay(null);
      setUnavailable(true);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadMemory();
  }, []);

  if (loading && !day) {
    return <p className={chromeTextClass}>LOADING MEMORY</p>;
  }

  if (unavailable) {
    return (
      <section className="max-w-xl rounded-[2px] border-t border-hairline bg-cream-paper py-5">
        <p className={cx(chromeTextClass, "text-[11px] text-slate")}>MEMORY UNAVAILABLE</p>
      </section>
    );
  }

  if (!day) {
    return null;
  }

  return (
    <article
      aria-label="MEMORY CARD"
      className="grid max-w-xl gap-4 rounded-[2px] border-t border-hairline bg-cream-paper py-5"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className={cx(chromeTextClass, "text-[11px] text-slate")}>
          {`MEMORY · ${day.date} · ${day.weekday}`}
        </h3>
        <Tag>{day.mood.toUpperCase()}</Tag>
      </div>
      <p className={cx(readingTextClass, "text-base leading-7")}>{day.summary}</p>
      <div className="flex flex-wrap gap-2">
        {day.key_topics.map((topic) => (
          <Tag key={topic}>{topic.toUpperCase()}</Tag>
        ))}
      </div>
      <div>
        <Button disabled={loading} onClick={loadMemory}>
          ANOTHER
        </Button>
      </div>
      <a className={cx(chromeTextClass, "text-[10px] text-schematic-blue")} href={`#journal/${day.date}`}>
        READ THIS DAY
      </a>
    </article>
  );
}
