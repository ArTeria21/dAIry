import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const day13 = {
  date: "2026-02-13",
  prev_date: "2026-02-12",
  next_date: "2026-02-14",
  day: {
    mood: "calm",
    mood_confidence: 0.82,
    summary: "A focused day of prototyping.",
    key_topics: ["work", "focus"],
    weekday: "FRIDAY",
    is_weekend: false,
    season: "winter",
    facts: { deep_focus: true },
  },
  notes: [
    {
      id: "2026-02-13T09:42",
      ts: "09:42",
      kind: "text",
      heading_display: "09:42 — text",
      raw_text: "Today I built the first map prototype.",
      mood: "calm",
      topics: ["work", "focus"],
      gist: "Built a prototype.",
    },
    {
      id: "2026-02-13T21:10",
      ts: "21:10",
      kind: null,
      heading_display: "February 13 21:10",
      raw_text: "Evening reflection.",
      mood: null,
      topics: [],
      gist: null,
    },
  ],
};

const day14 = {
  date: "2026-02-14",
  prev_date: "2026-02-13",
  next_date: null,
  day: null,
  notes: [
    {
      id: "2026-02-14T08:00",
      ts: "08:00",
      kind: "voice",
      heading_display: "08:00 — voice",
      raw_text: "A vault-only morning note.",
      mood: null,
      topics: [],
      gist: null,
    },
  ],
};

const monthPayload = {
  days: [
    { date: "2026-02-13", note_count: 2, mood: "calm" },
    { date: "2026-02-14", note_count: 1, mood: null },
  ],
};

function installFetchMock({
  latestStatus = 200,
}: {
  latestStatus?: number;
} = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();

    if (url.endsWith("/api/auth/me")) {
      return jsonResponse({ username: "artem" });
    }
    if (url.endsWith("/api/days/latest")) {
      return jsonResponse(day14, latestStatus);
    }
    if (url.endsWith("/api/days/2026-02-13")) {
      return jsonResponse(day13);
    }
    if (url.endsWith("/api/days/2026-02-14")) {
      return jsonResponse(day14);
    }
    if (url.includes("/api/days?month=2026-02")) {
      return jsonResponse(monthPayload);
    }

    return jsonResponse({ detail: `Unexpected request: ${url}` }, 500);
  });
}

async function renderJournal(hash = "#journal/2026-02-13") {
  window.location.hash = hash;
  render(<App />);
  return screen.findByRole("heading", { name: "JOURNAL" });
}

describe("Sprint 3 journal reader", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.location.hash = "";
  });

  afterEach(() => {
    window.location.hash = "";
  });

  it("renders a deep-linked journal day with summary, note blocks, chips, and month index", async () => {
    installFetchMock();

    await renderJournal();
    const article = await screen.findByRole("article", { name: "JOURNAL DAY 2026-02-13" });

    expect(within(article).getByText("2026-02-13")).toHaveClass("font-gerstnerprogramm");
    expect(within(article).getByText("A focused day of prototyping.")).toHaveClass("font-gerstnerprogramm");
    expect(within(article).getByText("09:42 · TEXT")).toHaveClass("font-ftsystemmono");
    expect(within(article).getByText("Today I built the first map prototype.")).toHaveClass("whitespace-pre-wrap");
    expect(within(article).getAllByText("CALM")).toHaveLength(2);
    expect(within(article).getAllByText("WORK")).toHaveLength(2);
    expect(within(article).getAllByText("FOCUS")).toHaveLength(2);
    expect(within(article).getByText("February 13 21:10")).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "JOURNAL MONTH INDEX" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /2026-02-14/ })).toBeInTheDocument();
  });

  it("prev next navigation and month index clicks update the journal hash", async () => {
    installFetchMock();

    await renderJournal();
    await userEvent.click(await screen.findByRole("button", { name: "NEXT DAY" }));

    await waitFor(() => {
      expect(window.location.hash).toBe("#journal/2026-02-14");
    });
    expect(await screen.findByRole("article", { name: "JOURNAL DAY 2026-02-14" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /2026-02-13/ }));
    await waitFor(() => {
      expect(window.location.hash).toBe("#journal/2026-02-13");
    });
  });

  it("invalid journal date params fall back to latest", async () => {
    const fetchMock = installFetchMock();

    await renderJournal("#journal/not-a-date");

    expect(await screen.findByRole("article", { name: "JOURNAL DAY 2026-02-14" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/days/latest", expect.objectContaining({ credentials: "include" }));
  });

  it("renders an empty journal state when latest day is unavailable", async () => {
    installFetchMock({ latestStatus: 404 });

    await renderJournal("#journal");

    expect(await screen.findByText("NO ENTRIES YET")).toBeInTheDocument();
  });
});
