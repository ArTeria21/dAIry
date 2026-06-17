import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const firstMemory = {
  day: {
    date: "2026-02-13",
    weekday: "FRIDAY",
    mood: "calm",
    key_topics: ["work", "focus"],
    summary: "A focused day of prototyping.",
    raw_text: "private transcript should never render",
    note_path: "/Users/artem/Vault/2026/02/2026-02-13.md",
  },
};

const secondMemory = {
  day: {
    date: "2026-02-14",
    weekday: "SATURDAY",
    mood: "joy",
    key_topics: ["home"],
    summary: "Dinner and home rituals.",
  },
};

function installFetchMock({
  responses = [firstMemory],
  status = 200,
}: {
  responses?: unknown[];
  status?: number;
} = {}) {
  let resurfaceIndex = 0;
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();

    if (url.endsWith("/api/auth/me")) {
      return jsonResponse({ username: "artem" });
    }
    if (url.endsWith("/api/resurface")) {
      const body = responses[Math.min(resurfaceIndex, responses.length - 1)];
      resurfaceIndex += 1;
      return jsonResponse(body, status);
    }

    return jsonResponse({ detail: `Unexpected request: ${url}` }, 500);
  });
}

async function renderAuthenticatedMemory() {
  window.location.hash = "#memory";
  render(<App />);
  return screen.findByRole("heading", { name: "MEMORY" });
}

describe("Phase 4 memory view", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.location.hash = "";
  });

  afterEach(() => {
    window.location.hash = "";
  });

  it("AC-5/AC-7: fetches /api/resurface on view open and renders only processed memory text", async () => {
    const fetchMock = installFetchMock();

    await renderAuthenticatedMemory();
    const card = await screen.findByRole("article", { name: "MEMORY CARD" });

    expect(fetchMock).toHaveBeenCalledWith("/api/resurface", expect.objectContaining({ credentials: "include" }));
    expect(within(card).getByText("MEMORY · 2026-02-13 · FRIDAY")).toBeInTheDocument();
    expect(within(card).getByText("A focused day of prototyping.")).toHaveClass(
      "font-gerstnerprogramm",
    );
    expect(screen.queryByText("private transcript should never render")).not.toBeInTheDocument();
    expect(screen.queryByText(/Users\/artem/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Vault/)).not.toBeInTheDocument();
  });

  it("AC-6: clicking ANOTHER fetches /api/resurface again and replaces the day", async () => {
    const fetchMock = installFetchMock({ responses: [firstMemory, secondMemory] });

    await renderAuthenticatedMemory();
    expect(await screen.findByText("MEMORY · 2026-02-13 · FRIDAY")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "ANOTHER" }));

    expect(await screen.findByText("MEMORY · 2026-02-14 · SATURDAY")).toBeInTheDocument();
    expect(screen.queryByText("MEMORY · 2026-02-13 · FRIDAY")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("ERR-1: renders a sanitized unavailable state when /api/resurface returns 404", async () => {
    installFetchMock({
      responses: [{ detail: "Missing /Users/artem/Vault/2026/02/2026-02-13.md" }],
      status: 404,
    });

    await renderAuthenticatedMemory();

    expect(await screen.findByText("MEMORY UNAVAILABLE")).toBeInTheDocument();
    expect(screen.queryByText(/Users\/artem/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Vault/)).not.toBeInTheDocument();
  });
});
