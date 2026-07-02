import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { moodPalette, topicHighlightColor, topicMutedColor } from "./design/palettes";

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
  ],
};

const timelinePayload = {
  buckets: [
    { period: "2026-W07", counts: { work: 2, home: 1 } },
    { period: "2026-W08", counts: { work: 1, home: 3 } },
  ],
};

const mapPayload = {
  signature: "notes:2:max:2",
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
  timeline?: typeof timelinePayload;
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

describe("Phase 4 seasons view", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.location.hash = "";
  });

  afterEach(() => {
    window.location.hash = "";
  });

  it("AC-1: renders calendar day cells with the shared mood palette colors", async () => {
    installFetchMock();

    await renderAuthenticatedSeasons();

    const calmDay = await screen.findByRole("button", { name: "2026-02-13" });
    const joyDay = screen.getByRole("button", { name: "2026-02-14" });
    expect(calmDay.style.getPropertyValue("--day-color")).toBe(moodPalette.calm);
    expect(joyDay.style.getPropertyValue("--day-color")).toBe(moodPalette.joy);
  });

  it("AC-2: clicking a day renders its serif summary and fact chips", async () => {
    installFetchMock();

    await renderAuthenticatedSeasons();
    await userEvent.click(await screen.findByRole("button", { name: "2026-02-13" }));
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

  it("AC-3: renders topic-frequency sparklines with mono labels", async () => {
    installFetchMock();

    await renderAuthenticatedSeasons();

    expect(await screen.findByText("WORK")).toHaveClass("font-plexmono");
    expect(screen.getByText("HOME")).toHaveClass("font-plexmono");
    expect(screen.getByTestId("topic-sparkline-work")).toHaveAttribute("stroke", "#0d6ea5");
    expect(screen.getByTestId("topic-sparkline-home")).toHaveAttribute("stroke", "#0d6ea5");
  });

  it("AC-4: selecting a topic highlights its sparkline and fades nonmatching calendar days", async () => {
    installFetchMock();

    await renderAuthenticatedSeasons();
    await userEvent.click(await screen.findByRole("button", { name: "WORK" }));

    expect(screen.getByTestId("topic-sparkline-work")).toHaveAttribute("stroke", topicHighlightColor);
    expect(screen.getByRole("button", { name: "2026-02-13" }).style.getPropertyValue("--day-color")).toBe(
      moodPalette.calm,
    );
    expect(screen.getByRole("button", { name: "2026-02-14" }).style.getPropertyValue("--day-color")).toBe(
      topicMutedColor,
    );
  });

  it("AC-7: does not render raw text, note paths, or filesystem-looking paths from calendar responses", async () => {
    installFetchMock();

    await renderAuthenticatedSeasons();

    expect(await screen.findByRole("button", { name: "2026-02-13" })).toBeInTheDocument();
    expect(screen.queryByText("private transcript should never render")).not.toBeInTheDocument();
    expect(screen.queryByText("another private transcript")).not.toBeInTheDocument();
    expect(screen.queryByText(/Users\/artem/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Vault/)).not.toBeInTheDocument();
  });

  it("EC-1/EC-2: renders empty calendar and empty topic states", async () => {
    installFetchMock({
      calendar: { days: [] },
      timeline: { buckets: [] },
      map: { ...mapPayload, points: [] },
    });

    await renderAuthenticatedSeasons();

    expect(await screen.findByText("NO DAYS TO SHOW")).toBeInTheDocument();
    expect(screen.queryAllByRole("button", { name: /^\d{4}-\d{2}-\d{2}$/ })).toHaveLength(0);
    expect(screen.getByText("NO TOPIC SIGNAL")).toBeInTheDocument();
    expect(screen.queryByTestId(/^topic-sparkline-/)).not.toBeInTheDocument();
  });
});
