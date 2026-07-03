import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { moodPalette } from "./design/palettes";
import { calendarHeatmapMetrics, noMoodColor } from "./seasons/CalendarHeatmap";
import { buildTopicStreamModel } from "./seasons/TopicsStream";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const calendarPayload = {
  days: [
    {
      date: "2026-02-13",
      weekday: "FRIDAY",
      is_weekend: false,
      season: "winter",
      mood: "calm",
      mood_confidence: 0.82,
      summary: "A focused day of prototyping.",
      facts: {
        sleep_quality: 4,
        sport: true,
        deep_focus: true,
        reading: false,
        purchases: false,
        eating_outside: false,
      },
      raw_text: "private transcript should never render",
      note_path: "/Users/artem/Vault/2026/02/2026-02-13.md",
    },
    {
      date: "2026-02-14",
      weekday: "SATURDAY",
      is_weekend: true,
      season: "winter",
      mood: "joy",
      mood_confidence: 0.74,
      summary: "Dinner and home rituals.",
      facts: {
        sleep_quality: 3,
        sport: false,
        deep_focus: false,
        reading: true,
        purchases: false,
        eating_outside: true,
      },
      raw_text: "another private transcript",
      note_path: "/Users/artem/Vault/2026/02/2026-02-14.md",
    },
    {
      date: "2026-02-16",
      weekday: "MONDAY",
      is_weekend: false,
      season: "winter",
      mood: null,
      mood_confidence: null,
      summary: "Vault-only day without mood.",
      facts: {},
      raw_text: "unprocessed private transcript",
      note_path: "/Users/artem/Vault/2026/02/2026-02-16.md",
    },
  ],
};

const timelinePayload = {
  buckets: [
    {
      period: "2026-W07",
      total: 6,
      counts: {
        work: 3,
        home: 1,
        reflection: 5,
        learning: 2,
        health: 1,
        travel: 1,
        admin: 1,
        creative: 1,
        friends: 1,
        finance: 1,
      },
    },
    {
      period: "2026-W08",
      total: 5,
      counts: {
        work: 1,
        home: 3,
        reflection: 2,
        learning: 1,
        health: 1,
        travel: 1,
        admin: 1,
        creative: 1,
        friends: 1,
      },
    },
    {
      period: "2026-W09",
      total: 4,
      counts: {
        work: 2,
        home: 1,
        learning: 1,
        health: 1,
        travel: 1,
        admin: 1,
        creative: 1,
        friends: 1,
        finance: 1,
      },
    },
  ],
};

const shortTimelinePayload = {
  buckets: [
    { period: "2026-W07", total: 2, counts: { work: 2, reflection: 1 } },
    { period: "2026-W08", total: 1, counts: { home: 1 } },
  ],
};

const malformedTimelinePayload = {
  buckets: [
    { period: "2026-W07", counts: { work: "abc" } },
    { period: "2026-W08", total: "5", counts: { work: "2" } },
    { period: "2026-W09", total: 0, counts: { work: 1 } },
  ],
};

const mapPayload = {
  signature: "notes:3:max:2",
  computed_at: "2026-06-17T08:00:00Z",
  points: [
    {
      id: 1,
      x: 0.2,
      y: 0.7,
      cluster_id: 0,
      mood: "calm",
      topics: ["work"],
      gist: "Built a map prototype.",
      date: "2026-02-13",
      ts: "2026-02-13T09:42:00Z",
    },
    {
      id: 2,
      x: 0.8,
      y: 0.3,
      cluster_id: 1,
      mood: "joy",
      topics: ["home"],
      gist: "Cooked dinner with friends.",
      date: "2026-02-14",
      ts: "2026-02-14T21:10:00Z",
    },
  ],
  clusters: [],
};

function installFetchMock({
  calendar = calendarPayload,
  timeline = timelinePayload,
  map = mapPayload,
}: {
  calendar?: typeof calendarPayload;
  timeline?: typeof timelinePayload | typeof shortTimelinePayload | typeof malformedTimelinePayload | { buckets: [] };
  map?: typeof mapPayload;
} = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();

    if (url.endsWith("/api/auth/me")) {
      return jsonResponse({ username: "artem" });
    }
    if (url.includes("/api/calendar")) {
      return jsonResponse(calendar);
    }
    if (url.endsWith("/api/topics/timeline?bucket=week")) {
      return jsonResponse(timeline);
    }
    if (url.endsWith("/api/map")) {
      return jsonResponse(map);
    }

    return jsonResponse({ detail: `Unexpected request: ${url}` }, 500);
  });
}

async function renderAuthenticatedSeasons() {
  window.location.hash = "#seasons";
  render(<App />);
  return screen.findByRole("heading", { name: "SEASONS" });
}

function fillOpacity(element: Element): number {
  return Number(element.getAttribute("fill-opacity"));
}

describe("Sprint 5 seasons view", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.location.hash = "";
  });

  afterEach(() => {
    window.location.hash = "";
  });

  it("renders a real mood heatmap with empty cells, mood colors, and confidence opacity", async () => {
    installFetchMock();

    await renderAuthenticatedSeasons();

    const calmDay = await screen.findByRole("button", { name: "2026-02-13 · CALM" });
    const joyDay = screen.getByRole("button", { name: "2026-02-14 · JOY" });
    const noMoodDay = screen.getByRole("button", { name: "2026-02-16 · NO MOOD" });
    const emptyDay = screen.getByTestId("calendar-cell-2026-02-15");

    expect(calmDay).toHaveAttribute("fill", moodPalette.calm);
    expect(fillOpacity(calmDay)).toBeCloseTo(0.883);
    expect(joyDay).toHaveAttribute("fill", moodPalette.joy);
    expect(noMoodDay).toHaveAttribute("fill", noMoodColor);
    expect(fillOpacity(noMoodDay)).toBeCloseTo(0.675);
    expect(emptyDay).toHaveAttribute("fill", "transparent");
    expect(emptyDay).toHaveAttribute("stroke", "#e5e5e5");
    expect(emptyDay).toHaveAttribute("stroke-width", "1");
    expect(emptyDay).not.toHaveAttribute("role");
    expect(screen.getByTestId("calendar-year-2026")).toBeInTheDocument();
  });

  it("clicking a heatmap day renders its serif summary, facts, and reader link", async () => {
    installFetchMock();

    await renderAuthenticatedSeasons();
    await userEvent.click(await screen.findByRole("button", { name: "2026-02-13 · CALM" }));
    const panel = await screen.findByRole("complementary", { name: "DAY 2026-02-13 DETAILS" });

    expect(within(panel).getByText("A focused day of prototyping.")).toHaveClass(
      "font-gerstnerprogramm",
    );
    expect(within(panel).getByRole("link", { name: "READ THIS DAY" })).toHaveAttribute(
      "href",
      "#journal/2026-02-13",
    );
    expect(within(panel).getByText("SLEEP 4/5")).toBeInTheDocument();
    expect(within(panel).getByText("SPORT YES")).toBeInTheDocument();
    expect(within(panel).getByText("DEEP FOCUS YES")).toBeInTheDocument();
  });

  it("filters heatmap days by selected mood and selected topic together", async () => {
    installFetchMock();

    await renderAuthenticatedSeasons();
    await userEvent.click(await screen.findByRole("button", { name: "CALM" }));
    await userEvent.click(screen.getByRole("button", { name: "WORK" }));

    expect(fillOpacity(screen.getByRole("button", { name: "2026-02-13 · CALM" }))).toBeCloseTo(0.883);
    expect(fillOpacity(screen.getByRole("button", { name: "2026-02-14 · JOY" }))).toBe(0.15);
    expect(fillOpacity(screen.getByRole("button", { name: "2026-02-16 · NO MOOD" }))).toBe(0.15);
  });

  it("renders a d3-shape stream graph with top topics, OTHER, and no reflection layer", async () => {
    installFetchMock();

    await renderAuthenticatedSeasons();

    expect(await screen.findByTestId("topic-stream-layer-work")).toBeInTheDocument();
    expect(screen.getByTestId("topic-stream-layer-OTHER")).toBeInTheDocument();
    expect(screen.queryByTestId("topic-stream-layer-reflection")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "OTHER" })).toBeDisabled();
    expect(screen.queryAllByTestId(/^topic-sparkline-/)).toHaveLength(0);
  });

  it("selecting a stream topic highlights the layer and filters the calendar", async () => {
    installFetchMock();

    await renderAuthenticatedSeasons();
    await userEvent.click(await screen.findByRole("button", { name: "WORK" }));

    expect(screen.getByTestId("topic-stream-layer-work")).toHaveAttribute("stroke", "#181818");
    expect(screen.getByTestId("topic-stream-layer-home")).toHaveAttribute("fill-opacity", "0.28");
    expect(fillOpacity(screen.getByRole("button", { name: "2026-02-13 · CALM" }))).toBeCloseTo(0.883);
    expect(fillOpacity(screen.getByRole("button", { name: "2026-02-14 · JOY" }))).toBe(0.15);
  });

  it("shows a per-week stream tooltip for the nearest bucket and hides it on leave", async () => {
    installFetchMock();

    await renderAuthenticatedSeasons();
    const workLayer = await screen.findByTestId("topic-stream-layer-work");
    const graph = screen.getByLabelText("TOPIC STREAM GRAPH");

    expect(graph).toHaveClass("overflow-visible");
    expect(workLayer.querySelector("title")).toBeNull();

    fireEvent.pointerMove(workLayer, { clientX: 360, clientY: 80 });

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "WORK · WEEK OF 2026-02-16 · 1 NOTES (20%)",
    );

    fireEvent.pointerLeave(graph);

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("coerces malformed timeline totals and counts before building stream paths", async () => {
    installFetchMock({ timeline: malformedTimelinePayload });

    await renderAuthenticatedSeasons();

    const workLayer = await screen.findByTestId("topic-stream-layer-work");
    expect(workLayer.getAttribute("d")).not.toContain("NaN");
  });

  it("keeps no-leak guarantees and handles short or empty topic/calendar responses", async () => {
    installFetchMock({ timeline: shortTimelinePayload });

    await renderAuthenticatedSeasons();

    expect(await screen.findByText("NOT ENOUGH DATA")).toBeInTheDocument();
    expect(screen.queryByText("private transcript should never render")).not.toBeInTheDocument();
    expect(screen.queryByText("another private transcript")).not.toBeInTheDocument();
    expect(screen.queryByText(/Users\/artem/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Vault/)).not.toBeInTheDocument();
  });

  it("renders empty calendar and empty topic states", async () => {
    installFetchMock({
      calendar: { days: [] },
      timeline: { buckets: [] },
      map: { ...mapPayload, points: [] },
    });

    await renderAuthenticatedSeasons();

    expect(await screen.findByText("NO DAYS TO SHOW")).toBeInTheDocument();
    expect(screen.getByText("NO TOPIC SIGNAL")).toBeInTheDocument();
    expect(screen.queryAllByTestId(/^topic-stream-layer-/)).toHaveLength(0);
  });

  it("keeps heatmap cell sizing within the 1200px layout budget", () => {
    expect(calendarHeatmapMetrics.cellSize).toBeLessThanOrEqual(14);
    expect(calendarHeatmapMetrics.cellGap).toBeLessThanOrEqual(3);
  });

  it("treats zero-total topic buckets as zero share", () => {
    const model = buildTopicStreamModel([{ period: "2026-W07", total: 0, counts: { work: 3 } }]);

    expect(model.series.map((series) => series.key)).toEqual(["work"]);
    expect(model.data[0].shares.work).toBe(0);
  });

  it("fills missing ISO weeks so stream x positions stay calendar-linear", () => {
    const model = buildTopicStreamModel([
      { period: "2026-W01", total: 1, counts: { work: 1 } },
      { period: "2026-W02", total: 1, counts: { work: 1 } },
      { period: "2026-W03", total: 1, counts: { work: 1 } },
      { period: "2026-W04", total: 1, counts: { work: 1 } },
      { period: "2026-W20", total: 1, counts: { home: 1 } },
      { period: "2026-W21", total: 1, counts: { home: 1 } },
      { period: "2026-W22", total: 1, counts: { home: 1 } },
      { period: "2026-W23", total: 1, counts: { home: 1 } },
    ]);

    expect(model.data).toHaveLength(23);
    expect(model.data.map((datum) => datum.period).slice(0, 5)).toEqual([
      "2026-W01",
      "2026-W02",
      "2026-W03",
      "2026-W04",
      "2026-W05",
    ]);
    expect(model.data[4]).toMatchObject({
      period: "2026-W05",
      counts: { work: 0, home: 0 },
      shares: { work: 0, home: 0 },
    });
    expect(model.data.at(-1)?.period).toBe("2026-W23");
  });
});
