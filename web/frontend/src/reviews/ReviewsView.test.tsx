import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { ReviewsView } from "./ReviewsView";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const archive = {
  reviews: [
    {
      kind: "week",
      period: "2026-07-26",
      start_date: "2026-07-26",
      end_date: "2026-08-01",
      title: "A week of recalibration",
      counts: { entry_count: 3, active_days: 2 },
      has_image: true,
      language: "EN",
      version: 1,
      updated_at: "2026-08-02T09:00:00+02:00",
    },
  ],
};

function detail(overrides: Record<string, unknown> = {}) {
  return {
    kind: "week",
    period: "2026-07-26",
    start_date: "2026-07-26",
    end_date: "2026-08-01",
    title: "A week of recalibration",
    paragraphs: [
      {
        text: "Pressure appeared alongside a quieter form of agency.",
        evidence: [
          {
            id: "diary:2026-07-31T09:00",
            type: "diary",
            label: "31 Jul, 09:00",
            href: "#journal/2026-07-31",
          },
          {
            id: "vault:projects/idea.md#overview",
            type: "vault",
            label: "projects/idea.md",
            href: null,
          },
        ],
      },
    ],
    reflection_question: "What changes if uncertainty is allowed to remain open?",
    safety_note: "If immediate danger returns, contact local emergency support now.",
    counts: { entry_count: 3, active_days: 2 },
    image: {
      url: "/api/reviews/week/2026-07-26/image",
      alt: "An archival abstract weekly poster",
    },
    language: "EN",
    model: "test/model",
    version: 1,
    created_at: "2026-08-02T09:00:00+02:00",
    updated_at: "2026-08-02T09:00:00+02:00",
    ...overrides,
  };
}

function installFetch(reviewDetail = detail(), reviewArchive = archive) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/api/reviews/capabilities") {
      return jsonResponse({ regenerate: true });
    }
    if (url.includes("/api/reviews?") && url.includes("kind=week")) {
      return jsonResponse(reviewArchive);
    }
    if (url === "/api/reviews/week/2026-07-26") {
      return jsonResponse(reviewDetail);
    }
    return jsonResponse({ detail: `Unexpected request ${url}` }, 500);
  });
}

describe("weekly and monthly reviews", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.location.hash = "";
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("AC-5.1 renders a serif essay, poster, safe evidence links, safety note and open question", async () => {
    installFetch();

    render(<ReviewsView kind="week" period="2026-07-26" />);

    expect(await screen.findByRole("heading", { name: "A week of recalibration" })).toBeInTheDocument();
    expect(screen.getByText("Pressure appeared alongside a quieter form of agency.")).toHaveClass(
      "font-gerstnerprogramm",
    );
    expect(screen.getByRole("img", { name: "An archival abstract weekly poster" })).toHaveAttribute(
      "src",
      "/api/reviews/week/2026-07-26/image",
    );
    expect(screen.getByRole("link", { name: "31 Jul, 09:00" })).toHaveAttribute(
      "href",
      "#journal/2026-07-31",
    );
    expect(screen.getByText("projects/idea.md").closest("a")).toBeNull();
    expect(screen.getByText(/If immediate danger returns/)).toBeInTheDocument();
    expect(screen.getByText("What changes if uncertainty is allowed to remain open?")).toBeInTheDocument();
  });

  it("AC-5.2 exposes week/month deep links and archive navigation", async () => {
    installFetch();
    render(<ReviewsView kind="week" period="2026-07-26" />);

    expect(await screen.findByRole("navigation", { name: "REVIEW PERIOD TYPE" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "WEEK" })).toHaveAttribute("href", "#reviews/week");
    expect(screen.getByRole("link", { name: "MONTH" })).toHaveAttribute("href", "#reviews/month");
    const archiveNav = screen.getByRole("navigation", { name: "REVIEW ARCHIVE" });
    expect(within(archiveNav).getByRole("link", { name: /2026-07-26/ })).toHaveAttribute(
      "href",
      "#reviews/week/2026-07-26",
    );
  });

  it("EC-5.1 renders a complete text-only review without a broken image", async () => {
    installFetch(detail({ image: null, safety_note: null }));
    render(<ReviewsView kind="week" period="2026-07-26" />);

    expect(await screen.findByText("Pressure appeared alongside a quieter form of agency.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("POSTER UNAVAILABLE")).toBeInTheDocument();
  });

  it("AC-4.2 keeps the essay visible and hides regeneration when capability is absent", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/reviews/capabilities") {
        return jsonResponse({ regenerate: false });
      }
      if (url.includes("/api/reviews?")) return jsonResponse(archive);
      if (url === "/api/reviews/week/2026-07-26") return jsonResponse(detail());
      return jsonResponse({}, 500);
    });

    render(<ReviewsView kind="week" period="2026-07-26" />);

    expect(await screen.findByRole("heading", { name: "A week of recalibration" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "REGENERATE" })).not.toBeInTheDocument();
  });

  it("AC-5.3 regenerates once, polls the job, disables the button, then refetches", async () => {
    let detailCalls = 0;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/reviews/capabilities") return jsonResponse({ regenerate: true });
      if (url.includes("/api/reviews?")) return jsonResponse(archive);
      if (url === "/api/reviews/week/2026-07-26" && !init?.method) {
        detailCalls += 1;
        return jsonResponse(detail({ title: detailCalls > 1 ? "A renewed week" : "A week of recalibration" }));
      }
      if (url.endsWith("/regenerate") && init?.method === "POST") {
        return jsonResponse({ job_id: 73, status: "pending" }, 202);
      }
      if (url === "/api/review-jobs/73") {
        return jsonResponse({ job_id: 73, status: "complete" });
      }
      return jsonResponse({}, 500);
    });
    render(<ReviewsView kind="week" period="2026-07-26" />);
    const button = await screen.findByRole("button", { name: "REGENERATE" });

    vi.useFakeTimers();
    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(screen.getByText("REGENERATING")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });

    expect(screen.getByRole("heading", { name: "A renewed week" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/reviews/week/2026-07-26/regenerate",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
    expect(detailCalls).toBe(2);
  });

  it("AC-4.4 reports regeneration failure locally without hiding the ready review", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/reviews/capabilities") return jsonResponse({ regenerate: true });
      if (url.includes("/api/reviews?")) return jsonResponse(archive);
      if (url === "/api/reviews/week/2026-07-26") return jsonResponse(detail());
      if (url.endsWith("/regenerate") && init?.method === "POST") {
        return jsonResponse({ detail: "generation failed" }, 503);
      }
      return jsonResponse({}, 500);
    });
    render(<ReviewsView kind="week" period="2026-07-26" />);
    const button = await screen.findByRole("button", { name: "REGENERATE" });

    fireEvent.click(button);

    expect(await screen.findByRole("alert", { name: "REGENERATION ERROR" })).toHaveTextContent(
      "REGENERATION FAILED",
    );
    expect(screen.getByRole("heading", { name: "A week of recalibration" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "An archival abstract weekly poster" })).toBeInTheDocument();
    expect(screen.queryByText("REVIEW UNAVAILABLE")).not.toBeInTheDocument();
  });

  it("AC-4.3 scopes polling to kind, period and operation so WEEK cannot mutate MONTH", async () => {
    const monthArchive = {
      reviews: [
        {
          ...archive.reviews[0],
          kind: "month",
          period: "2026-07",
          start_date: "2026-07-01",
          end_date: "2026-07-31",
          title: "A month in motion",
        },
      ],
    };
    let resolveOldJob!: (response: Response) => void;
    const oldJob = new Promise<Response>((resolve) => {
      resolveOldJob = resolve;
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/reviews/capabilities") return jsonResponse({ regenerate: true });
      if (url.includes("kind=week")) return jsonResponse(archive);
      if (url.includes("kind=month")) return jsonResponse(monthArchive);
      if (url === "/api/reviews/week/2026-07-26") return jsonResponse(detail());
      if (url === "/api/reviews/month/2026-07") {
        return jsonResponse(
          detail({
            kind: "month",
            period: "2026-07",
            start_date: "2026-07-01",
            end_date: "2026-07-31",
            title: "A month in motion",
            image: null,
          }),
        );
      }
      if (url.endsWith("/regenerate") && init?.method === "POST") {
        return jsonResponse({ job_id: 91, status: "pending" }, 202);
      }
      if (url === "/api/review-jobs/91") return oldJob;
      return jsonResponse({}, 500);
    });
    const view = render(<ReviewsView kind="week" period="2026-07-26" />);
    const button = await screen.findByRole("button", { name: "REGENERATE" });
    vi.useFakeTimers();
    fireEvent.click(button);

    view.rerender(<ReviewsView kind="month" period="2026-07" />);
    await act(async () => undefined);
    expect(screen.getByRole("heading", { name: "A month in motion" })).toBeInTheDocument();
    expect(screen.queryByText("REGENERATING")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    resolveOldJob(jsonResponse({ job_id: 91, status: "failed" }));
    await act(async () => undefined);

    expect(screen.getByRole("heading", { name: "A month in motion" })).toBeInTheDocument();
    expect(screen.queryByText("REGENERATION FAILED")).not.toBeInTheDocument();
    expect(screen.queryByText("REVIEW UNAVAILABLE")).not.toBeInTheDocument();
    expect(screen.queryByText("REGENERATING")).not.toBeInTheDocument();
  });

  it("ERR-5.1 ignores a stale detail response after the deep link changes", async () => {
    let resolveOld!: (response: Response) => void;
    const oldResponse = new Promise<Response>((resolve) => {
      resolveOld = resolve;
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/reviews?")) return jsonResponse(archive);
      if (url.endsWith("2026-07-26")) return oldResponse;
      if (url.endsWith("2026-08-02")) {
        return jsonResponse(
          detail({ period: "2026-08-02", title: "The newer week", image: null }),
        );
      }
      return jsonResponse({}, 500);
    });
    const view = render(<ReviewsView kind="week" period="2026-07-26" />);
    view.rerender(<ReviewsView kind="week" period="2026-08-02" />);
    expect(await screen.findByRole("heading", { name: "The newer week" })).toBeInTheDocument();

    resolveOld(jsonResponse(detail({ title: "Stale old week" })));
    await act(async () => undefined);
    expect(screen.queryByText("Stale old week")).not.toBeInTheDocument();
  });

  it("AC-5.4 adds REVIEWS routing and a wrapping mobile-safe navigation", async () => {
    window.location.hash = "#reviews/week/2026-07-26";
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) return jsonResponse({ username: "artem" });
      if (url.includes("/api/reviews?")) return jsonResponse(archive);
      if (url.endsWith("/api/reviews/week/2026-07-26")) return jsonResponse(detail());
      return jsonResponse({}, 500);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "REVIEWS" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "REVIEWS" })).toHaveAttribute("href", "#reviews");
    expect(screen.getByRole("navigation", { name: "PRIMARY" })).toHaveClass("flex-wrap");
  });
});
