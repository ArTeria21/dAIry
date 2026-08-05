import { useCallback, useEffect, useRef, useState } from "react";

import { chromeTextClass, readingTextClass } from "../design/theme";
import {
  fetchReview,
  fetchReviewArchive,
  fetchReviewCapabilities,
  fetchReviewJob,
  regenerateReview,
  type ReviewArchiveItem,
  type ReviewDetail,
  type ReviewKind,
} from "../services/reviews";
import { cx } from "../ui/classNames";

export function ReviewsView({
  kind = "week",
  period,
}: {
  kind?: ReviewKind;
  period?: string;
}) {
  const [archive, setArchive] = useState<ReviewArchiveItem[]>([]);
  const [review, setReview] = useState<ReviewDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [canRegenerate, setCanRegenerate] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [regenerationError, setRegenerationError] = useState<string | null>(null);
  const generationRef = useRef(0);
  const operationIdRef = useRef(0);
  const operationRef = useRef<RegenerationOperation | null>(null);

  const load = useCallback(async () => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    setLoading(true);
    setUnavailable(false);
    try {
      const archivePayload = await fetchReviewArchive(kind);
      if (generationRef.current !== generation) return;
      setArchive(archivePayload.reviews);
      const targetPeriod = period ?? archivePayload.reviews[0]?.period;
      if (!targetPeriod) {
        setReview(null);
        return;
      }
      const detail = await fetchReview(kind, targetPeriod);
      if (generationRef.current === generation) {
        setReview(detail);
      }
    } catch {
      if (generationRef.current === generation) {
        setReview(null);
        setUnavailable(true);
      }
    } finally {
      if (generationRef.current === generation) {
        setLoading(false);
      }
    }
  }, [kind, period]);

  useEffect(() => {
    void load();
    return () => {
      generationRef.current += 1;
    };
  }, [load]);

  useEffect(() => {
    let active = true;
    void fetchReviewCapabilities()
      .then((capabilities) => {
        if (active) setCanRegenerate(capabilities.regenerate);
      })
      .catch(() => {
        if (active) setCanRegenerate(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    operationRef.current?.controller.abort();
    operationRef.current = null;
    setRegenerating(false);
    setRegenerationError(null);
    return () => {
      operationRef.current?.controller.abort();
      operationRef.current = null;
    };
  }, [kind, period]);

  async function handleRegenerate() {
    if (!review || !canRegenerate || regenerating) return;
    const operation: RegenerationOperation = {
      id: operationIdRef.current + 1,
      kind: review.kind,
      period: review.period,
      controller: new AbortController(),
    };
    operationIdRef.current = operation.id;
    operationRef.current = operation;
    setRegenerationError(null);
    setRegenerating(true);
    try {
      let job = await regenerateReview(
        operation.kind,
        operation.period,
        operation.controller.signal,
      );
      while (job.status === "pending" || job.status === "running") {
        await wait(500, operation.controller.signal);
        job = await fetchReviewJob(job.job_id, operation.controller.signal);
      }
      if (job.status !== "complete") {
        throw new Error("REGENERATION FAILED");
      }
      if (!isCurrentOperation(operationRef.current, operation)) return;
      await load();
    } catch {
      if (isCurrentOperation(operationRef.current, operation)) {
        setRegenerationError("REGENERATION FAILED");
      }
    } finally {
      if (isCurrentOperation(operationRef.current, operation)) {
        operationRef.current = null;
        setRegenerating(false);
      }
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
      <section className="grid content-start gap-6">
        <nav aria-label="REVIEW PERIOD TYPE" className="flex flex-wrap gap-2">
          {(["week", "month"] as const).map((item) => (
            <a
              aria-current={kind === item ? "page" : undefined}
              className={cx(
                chromeTextClass,
                "rounded-[2px] border px-3 py-2 text-[10px]",
                kind === item
                  ? "border-schematic-blue text-ink-black"
                  : "border-hairline text-slate",
              )}
              href={`#reviews/${item}`}
              key={item}
            >
              {item.toUpperCase()}
            </a>
          ))}
        </nav>

        {loading && !review ? <p className={chromeTextClass}>LOADING REVIEW</p> : null}
        {!loading && unavailable ? (
          <p className={cx(chromeTextClass, "text-[11px] text-slate")} role="alert">
            REVIEW UNAVAILABLE
          </p>
        ) : null}
        {!loading && !unavailable && !review ? (
          <p className={cx(chromeTextClass, "text-[11px] text-slate")}>NO REVIEWS YET</p>
        ) : null}
        {review ? (
          <ReviewEssay
            canRegenerate={canRegenerate}
            onRegenerate={handleRegenerate}
            regenerationError={regenerationError}
            regenerating={regenerating}
            review={review}
          />
        ) : null}
      </section>

      <ReviewArchive archive={archive} current={review?.period} kind={kind} />
    </div>
  );
}

function ReviewEssay({
  review,
  canRegenerate,
  regenerating,
  regenerationError,
  onRegenerate,
}: {
  review: ReviewDetail;
  canRegenerate: boolean;
  regenerating: boolean;
  regenerationError: string | null;
  onRegenerate: () => void;
}) {
  return (
    <article aria-label={`${review.kind.toUpperCase()} REVIEW`} className="grid gap-7">
      <header className="grid gap-2 border-b border-hairline pb-5">
        <p className={cx(chromeTextClass, "text-[10px] text-slate")}>
          {review.start_date} — {review.end_date}
        </p>
        {review.status === "stale" ? (
          <p className={cx(chromeTextClass, "text-[10px] text-schematic-blue")}>
            UPDATING — SHOWING PREVIOUS VERSION
          </p>
        ) : null}
        <h3 className={cx(readingTextClass, "text-3xl font-medium leading-[1.11]")}>
          {review.title}
        </h3>
      </header>

      {review.image ? (
        <img
          alt={review.image.alt}
          className="aspect-square w-full max-w-[760px] rounded-[2px] border border-hairline object-cover"
          src={review.image.url}
        />
      ) : (
        <p className={cx(chromeTextClass, "text-[10px] text-slate")}>POSTER UNAVAILABLE</p>
      )}

      <div className="grid max-w-3xl gap-6">
        {review.paragraphs.map((paragraph, index) => (
          <section className="grid gap-2" key={`${review.period}-${index}`}>
            <p className={cx(readingTextClass, "text-[17px] leading-8")}>{paragraph.text}</p>
            {paragraph.evidence.length > 0 ? (
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {paragraph.evidence.map((evidence) =>
                  evidence.href ? (
                    <a
                      className={cx(chromeTextClass, "text-[9px] text-schematic-blue")}
                      href={evidence.href}
                      key={evidence.id}
                    >
                      {evidence.label}
                    </a>
                  ) : (
                    <span
                      className={cx(chromeTextClass, "text-[9px] text-slate")}
                      key={evidence.id}
                    >
                      {evidence.label}
                    </span>
                  ),
                )}
              </div>
            ) : null}
          </section>
        ))}
      </div>

      {review.safety_note ? (
        <aside className="max-w-3xl border-l-2 border-signal-orange pl-4">
          <p className={cx(readingTextClass, "text-sm leading-6")}>{review.safety_note}</p>
        </aside>
      ) : null}

      <section className="grid max-w-3xl gap-2 border-t border-hairline pt-5">
        <h4 className={cx(chromeTextClass, "text-[10px] text-slate")}>OPEN QUESTION</h4>
        <p className={cx(readingTextClass, "text-xl italic leading-8")}>
          {review.reflection_question}
        </p>
      </section>

      {canRegenerate ? (
        <div className="flex items-center gap-3">
          <button
            className={cx(
              chromeTextClass,
              "rounded-[2px] border border-hairline px-3 py-2 text-[10px] disabled:text-slate",
            )}
            disabled={regenerating}
            onClick={onRegenerate}
            type="button"
          >
            REGENERATE
          </button>
          {regenerating ? (
            <span className={cx(chromeTextClass, "text-[10px] text-slate")}>REGENERATING</span>
          ) : null}
          {regenerationError ? (
            <span
              aria-label="REGENERATION ERROR"
              className={cx(chromeTextClass, "text-[10px] text-signal-orange")}
              role="alert"
            >
              {regenerationError}
            </span>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function ReviewArchive({
  archive,
  current,
  kind,
}: {
  archive: ReviewArchiveItem[];
  current?: string;
  kind: ReviewKind;
}) {
  return (
    <nav
      aria-label="REVIEW ARCHIVE"
      className="grid content-start gap-2 rounded-[2px] border border-hairline p-3"
    >
      <h3 className={cx(chromeTextClass, "mb-1 text-[10px] text-slate")}>ARCHIVE</h3>
      {archive.length === 0 ? (
        <p className={cx(chromeTextClass, "text-[10px] text-slate")}>NO PERIODS</p>
      ) : (
        archive.map((item) => (
          <a
            aria-current={item.period === current ? "page" : undefined}
            className={cx(
              chromeTextClass,
              "grid gap-1 rounded-[2px] border px-2 py-2 text-[10px]",
              item.period === current
                ? "border-schematic-blue text-ink-black"
                : "border-hairline text-slate",
            )}
            href={`#reviews/${kind}/${item.period}`}
            key={item.period}
          >
            <span>{item.period}</span>
            {item.status === "stale" ? (
              <span className="text-schematic-blue">UPDATING</span>
            ) : null}
            <span className="font-gerstnerprogramm normal-case">{item.title}</span>
          </a>
        ))
      )}
    </nav>
  );
}

type RegenerationOperation = {
  id: number;
  kind: ReviewKind;
  period: string;
  controller: AbortController;
};

function isCurrentOperation(
  current: RegenerationOperation | null,
  expected: RegenerationOperation,
): boolean {
  return current === expected && !expected.controller.signal.aborted;
}

function wait(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const finish = () => {
      signal.removeEventListener("abort", cancel);
      resolve();
    };
    const cancel = () => {
      window.clearTimeout(timeout);
      reject(new DOMException("Polling cancelled", "AbortError"));
    };
    const timeout = window.setTimeout(finish, milliseconds);
    if (signal.aborted) {
      cancel();
      return;
    }
    signal.addEventListener("abort", cancel, { once: true });
  });
}
