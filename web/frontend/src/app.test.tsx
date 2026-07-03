import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Phase 2 app shell and auth flow", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("AC-2: renders login screen with cream canvas, mono labels, and orange LOG IN button when unauthenticated", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      jsonResponse({ detail: "Authentication required" }, 401),
    );

    render(<App />);

    expect(await screen.findByRole("heading", { name: "dAIry" })).toBeInTheDocument();
    expect(screen.getByLabelText("USERNAME")).toBeInTheDocument();
    expect(screen.getByLabelText("PASSWORD")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "LOG IN" })).toHaveClass(
      "bg-signal-orange",
    );
    expect(screen.getByTestId("app-shell")).toHaveClass("bg-cream-paper");
  });

  it("AC-2: submits credentials to /api/auth/login and then shows authenticated shell", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ detail: "Authentication required" }, 401))
      .mockResolvedValueOnce(jsonResponse({ username: "artem" }))
      .mockResolvedValueOnce(jsonResponse({ username: "artem" }));

    render(<App />);
    await screen.findByLabelText("USERNAME");

    await userEvent.type(screen.getByLabelText("USERNAME"), "artem");
    await userEvent.type(screen.getByLabelText("PASSWORD"), "secret");
    await userEvent.click(screen.getByRole("button", { name: "LOG IN" }));

    await waitFor(() => {
      expect(screen.getByText("MAP")).toBeInTheDocument();
    });
    expect(screen.getByText("SEASONS")).toBeInTheDocument();
    expect(screen.getByText("MEMORY")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/auth/login",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "artem", password: "secret" }),
      }),
    );
  });

  it("AC-2: routes between JOURNAL, MAP, SEASONS, and MEMORY views in the authenticated shell", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();

      if (url.endsWith("/api/auth/me")) {
        return jsonResponse({ username: "artem" });
      }
      if (url.endsWith("/api/map")) {
        return jsonResponse({ signature: "empty", computed_at: "", n_noise: 0, points: [], clusters: [] });
      }
      if (url.endsWith("/api/days/latest")) {
        return jsonResponse({
          date: "2026-02-14",
          prev_date: null,
          next_date: null,
          day: null,
          notes: [],
        });
      }
      if (url.includes("/api/days?month=2026-02")) {
        return jsonResponse({ days: [{ date: "2026-02-14", note_count: 0, mood: null }] });
      }

      return jsonResponse({ detail: `Unexpected request: ${url}` }, 500);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "MAP" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "JOURNAL" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "JOURNAL" }));
    expect(await screen.findByRole("heading", { name: "JOURNAL" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "SEASONS" }));
    expect(screen.getByRole("heading", { name: "SEASONS" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "MEMORY" }));
    expect(screen.getByRole("heading", { name: "MEMORY" })).toBeInTheDocument();
  });

  it("ERR-1: keeps raw user password out of login error text", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ detail: "Authentication required" }, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: "Invalid" }, 401));

    render(<App />);
    await screen.findByLabelText("USERNAME");

    await userEvent.type(screen.getByLabelText("USERNAME"), "artem");
    await userEvent.type(screen.getByLabelText("PASSWORD"), "secret");
    await userEvent.click(screen.getByRole("button", { name: "LOG IN" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("LOGIN FAILED");
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret");
  });
});
