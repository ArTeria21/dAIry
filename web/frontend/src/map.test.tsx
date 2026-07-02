import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import {
  clusterColor,
  moodPalette,
  noiseColor,
  topicPointColor,
  topicPointHighlightColor,
  topicMutedColor,
} from "./design/palettes";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const mapPayload = {
  signature: "notes:2:max:2",
  computed_at: "2026-06-17T08:00:00Z",
  n_noise: 0,
  points: [
    {
      id: 1,
      x: 0.2,
      y: 0.7,
      cluster_id: 0,
      mood: "calm",
      topics: ["work", "focus"],
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
  clusters: [
    { id: 0, label: "work", size: 1, dominant_topics: ["work", "focus"] },
    { id: 1, label: "home", size: 1, dominant_topics: ["home"] },
  ],
};

const notePayload = {
  id: 1,
  date: "2026-02-13",
  ts: "2026-02-13T09:42:00Z",
  mood: "calm",
  mood_confidence: 0.82,
  mood_evidence: "The note is reflective and steady.",
  topics: ["work", "focus"],
  gist: "Built a map prototype.",
  raw_text: "Today I built the first map prototype from my journal embeddings.",
  raw_text_sha256: "note-sha",
  day_summary: "A focused day of prototyping.",
  note_path: "2026/02/2026-02-13.md",
};

function installFetchMock({
  map = mapPayload,
  note = notePayload,
  noteStatus = 200,
  preserveMapObject = false,
}: {
  map?: unknown;
  note?: unknown;
  noteStatus?: number;
  preserveMapObject?: boolean;
} = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();

    if (url.endsWith("/api/auth/me")) {
      return jsonResponse({ username: "artem" });
    }
    if (url.endsWith("/api/map")) {
      if (preserveMapObject) {
        return {
          ok: true,
          status: 200,
          headers: new Headers({ "Content-Type": "application/json" }),
          json: async () => map,
        } as Response;
      }
      return jsonResponse(map);
    }
    if (url.endsWith("/api/notes/1")) {
      return jsonResponse(note, noteStatus);
    }

    return jsonResponse({ detail: `Unexpected request: ${url}` }, 500);
  });
}

async function renderAuthenticatedMap() {
  window.location.hash = "#map";
  render(<App />);
  return screen.findByRole("heading", { name: "MAP" });
}

const mapViewportRect = {
  x: 0,
  y: 0,
  left: 0,
  top: 0,
  width: 640,
  height: 480,
  right: 640,
  bottom: 480,
  toJSON: () => ({}),
} as DOMRect;

function installMapViewportRect() {
  const original = HTMLElement.prototype.getBoundingClientRect;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function getBoundingClientRect(
    this: HTMLElement,
  ) {
    if (this.dataset.testid === "map-panel" || this.dataset.testid === "map-viewport") {
      return mapViewportRect;
    }

    return original.call(this);
  });
}

function mapWithPoints(points: Array<Partial<(typeof mapPayload.points)[number]> & { id: number; x: number; y: number }>) {
  return {
    ...mapPayload,
    signature: `notes:${points.length}:wide`,
    points: points.map((point) => ({
      cluster_id: 0,
      mood: "calm",
      topics: ["work"],
      gist: `Note ${point.id}`,
      date: "2026-02-13",
      ts: "2026-02-13T09:42:00Z",
      ...point,
    })),
    clusters: [{ id: 0, label: "work", size: points.length, dominant_topics: ["work"] }],
  };
}

function readPointCenter(point: HTMLElement) {
  const left = point.style.left;
  const top = point.style.top;
  const leftMatch = left.match(/^(-?\d+(?:\.\d+)?)%$/);
  const topMatch = top.match(/^(-?\d+(?:\.\d+)?)%$/);

  expect(leftMatch, `Expected point left to be a percent value, got "${left}"`).not.toBeNull();
  expect(topMatch, `Expected point top to be a percent value, got "${top}"`).not.toBeNull();

  return {
    x: (Number(leftMatch?.[1]) / 100) * mapViewportRect.width,
    y: (Number(topMatch?.[1]) / 100) * mapViewportRect.height,
  };
}

function expectPointInsideViewport(point: HTMLElement, padding = 12) {
  const center = readPointCenter(point);

  expect(center.x).toBeGreaterThanOrEqual(padding);
  expect(center.x).toBeLessThanOrEqual(mapViewportRect.width - padding);
  expect(center.y).toBeGreaterThanOrEqual(padding);
  expect(center.y).toBeLessThanOrEqual(mapViewportRect.height - padding);
  expect(Number.isFinite(center.x)).toBe(true);
  expect(Number.isFinite(center.y)).toBe(true);
}

function readViewportTransform(viewport: HTMLElement) {
  const transform = viewport.style.transform;
  const scaleMatch = transform.match(/scale\((-?\d+(?:\.\d+)?)\)/);
  const translateMatch = transform.match(/translate\((-?\d+(?:\.\d+)?)px,\s*(-?\d+(?:\.\d+)?)px\)/);

  return {
    scale: scaleMatch ? Number(scaleMatch[1]) : 1,
    x: translateMatch ? Number(translateMatch[1]) : 0,
    y: translateMatch ? Number(translateMatch[2]) : 0,
  };
}

function expectedZoomAt(
  current: ReturnType<typeof readViewportTransform>,
  cursorX: number,
  cursorY: number,
  deltaY: number,
) {
  const clampedDelta = Math.min(240, Math.max(-240, deltaY));
  const nextScale = Math.min(6, Math.max(0.5, current.scale * Math.exp(-clampedDelta * 0.0008)));
  const k = nextScale / current.scale;

  return {
    scale: nextScale,
    x: cursorX - (cursorX - current.x) * k,
    y: cursorY - (cursorY - current.y) * k,
  };
}

function formatPx(value: number) {
  return `${Number(value.toFixed(2))}px`;
}

describe("Phase 3 map view", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    installMapViewportRect();
    window.location.hash = "";
  });

  afterEach(() => {
    window.location.hash = "";
  });

  it("renders one point control per /api/map point with CLUSTER as the default color mode", async () => {
    installFetchMock();

    await renderAuthenticatedMap();

    const point1 = await screen.findByRole("button", { name: "NOTE 1" });
    const point2 = screen.getByRole("button", { name: "NOTE 2" });
    expect(screen.getAllByRole("button", { name: /^NOTE \d+$/ })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "CLUSTER" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByLabelText("CLUSTER LEGEND")).toBeInTheDocument();
    expect(screen.getByLabelText("CLUSTER LEGEND")).toHaveClass("min-h-[74px]");
    expect(screen.getByRole("button", { name: "WORK" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "HOME" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("button", { name: "UNCLUSTERED" })).not.toBeInTheDocument();
    expect(point1.style.getPropertyValue("--point-color")).toBe(clusterColor(0));
    expect(point2.style.getPropertyValue("--point-color")).toBe(clusterColor(1));
  });

  it("clicking CLUSTER legend chips highlights the selected cluster and toggles back", async () => {
    installFetchMock();

    await renderAuthenticatedMap();

    const point1 = await screen.findByRole("button", { name: "NOTE 1" });
    const point2 = screen.getByRole("button", { name: "NOTE 2" });

    await userEvent.click(screen.getByRole("button", { name: "WORK" }));

    expect(screen.getByRole("button", { name: "WORK" })).toHaveAttribute("aria-pressed", "true");
    expect(point1.style.getPropertyValue("--point-color")).toBe(clusterColor(0));
    expect(point2.style.getPropertyValue("--point-color")).toBe(topicMutedColor);
    expect(screen.getByTestId("cluster-hull-0").parentElement).toHaveAttribute("opacity", "1");
    expect(screen.getByTestId("cluster-hull-1").parentElement).toHaveAttribute("opacity", "0.28");

    await userEvent.click(screen.getByRole("button", { name: "WORK" }));

    expect(screen.getByRole("button", { name: "WORK" })).toHaveAttribute("aria-pressed", "false");
    expect(point2.style.getPropertyValue("--point-color")).toBe(clusterColor(1));
    expect(screen.getByTestId("cluster-hull-1").parentElement).toHaveAttribute("opacity", "1");
  });

  it("renders unclustered noise as neutral gray with an UNCLUSTERED legend chip", async () => {
    installFetchMock({
      map: {
        ...mapPayload,
        n_noise: 1,
        points: [
          ...mapPayload.points,
          {
            id: 3,
            x: 0.5,
            y: 0.5,
            cluster_id: -1,
            mood: "neutral",
            topics: ["loose"],
            gist: "Did not fit a cluster.",
            date: "2026-02-15",
            ts: "2026-02-15T10:00:00Z",
          },
        ],
      },
    });

    await renderAuthenticatedMap();

    const noisePoint = await screen.findByRole("button", { name: "NOTE 3" });
    expect(screen.getByRole("button", { name: "UNCLUSTERED" })).toBeInTheDocument();
    expect(screen.getByTestId("legend-swatch-cluster-unclustered").style.getPropertyValue("--legend-color")).toBe(
      noiseColor,
    );
    expect(noisePoint.style.getPropertyValue("--point-color")).toBe(noiseColor);

    await userEvent.click(screen.getByRole("button", { name: "HOME" }));
    expect(noisePoint.style.getPropertyValue("--point-color")).toBe(noiseColor);
  });

  it("renders only the UNCLUSTERED legend chip when every point is noise", async () => {
    installFetchMock({
      map: {
        ...mapPayload,
        n_noise: 2,
        points: [
          { ...mapPayload.points[0], cluster_id: -1 },
          { ...mapPayload.points[1], cluster_id: -1 },
        ],
        clusters: [],
      },
    });

    await renderAuthenticatedMap();

    expect(await screen.findByLabelText("CLUSTER LEGEND")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "UNCLUSTERED" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "WORK" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "NOTE 1" }).style.getPropertyValue("--point-color")).toBe(noiseColor);
    expect(screen.getByRole("button", { name: "NOTE 2" }).style.getPropertyValue("--point-color")).toBe(noiseColor);
  });

  it("treats legacy map payloads without n_noise as zero noise", async () => {
    const { n_noise: _nNoise, ...legacyPayload } = mapPayload;
    installFetchMock({ map: legacyPayload });

    await renderAuthenticatedMap();

    expect(await screen.findByLabelText("CLUSTER LEGEND")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "UNCLUSTERED" })).not.toBeInTheDocument();
  });

  it("renders thin Schematic Blue cluster hulls and uppercase mono labels", async () => {
    installFetchMock();

    await renderAuthenticatedMap();

    expect(await screen.findByTestId("cluster-hull-0")).toHaveAttribute("stroke", "#0d6ea5");
    expect(screen.getByTestId("cluster-hull-1")).toHaveAttribute("stroke", "#0d6ea5");
    expect(screen.getByTestId("cluster-hull-0").nextElementSibling).toHaveTextContent("WORK");
    expect(screen.getByTestId("cluster-hull-0").nextElementSibling).toHaveClass("font-plexmono");
    expect(screen.getByTestId("cluster-hull-1").nextElementSibling).toHaveTextContent("HOME");
    expect(screen.getByTestId("cluster-hull-1").nextElementSibling).toHaveClass("font-plexmono");
  });

  it("selecting MOOD recolors points with the shared mood palette", async () => {
    installFetchMock();

    await renderAuthenticatedMap();
    await userEvent.click(await screen.findByRole("button", { name: "MOOD" }));

    expect(screen.getByLabelText("MOOD LEGEND")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "NOTE 1" }).style.getPropertyValue("--point-color")).toBe(
      moodPalette.calm,
    );
    expect(screen.getByRole("button", { name: "NOTE 2" }).style.getPropertyValue("--point-color")).toBe(
      moodPalette.joy,
    );

    await userEvent.click(screen.getByRole("button", { name: "CALM" }));
    expect(screen.getByRole("button", { name: "CALM" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "NOTE 1" }).style.getPropertyValue("--point-color")).toBe(
      moodPalette.calm,
    );
    expect(screen.getByRole("button", { name: "NOTE 2" }).style.getPropertyValue("--point-color")).toBe(
      topicMutedColor,
    );
  });

  it("switching color modes resets highlight without changing the legend frame height", async () => {
    installFetchMock();

    await renderAuthenticatedMap();

    await userEvent.click(await screen.findByRole("button", { name: "WORK" }));
    expect(screen.getByRole("button", { name: "NOTE 2" }).style.getPropertyValue("--point-color")).toBe(
      topicMutedColor,
    );
    expect(screen.getByLabelText("CLUSTER LEGEND")).toHaveClass("min-h-[74px]");

    await userEvent.click(screen.getByRole("button", { name: "MOOD" }));

    expect(screen.getByLabelText("MOOD LEGEND")).toHaveClass("min-h-[74px]");
    expect(screen.getByRole("button", { name: "CALM" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "NOTE 2" }).style.getPropertyValue("--point-color")).toBe(
      moodPalette.joy,
    );
  });

  it("selecting TOPIC uses neutral point colors and fades nonmatching points when filtered", async () => {
    installFetchMock();

    await renderAuthenticatedMap();
    await userEvent.click(await screen.findByRole("button", { name: "TOPIC" }));

    expect(screen.getByLabelText("TOPIC LEGEND")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ALL TOPICS" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "NOTE 1" }).style.getPropertyValue("--point-color")).toBe(
      topicPointColor,
    );
    expect(screen.getByRole("button", { name: "NOTE 2" }).style.getPropertyValue("--point-color")).toBe(
      topicPointColor,
    );

    await userEvent.click(screen.getByRole("button", { name: "WORK" }));

    expect(screen.getByRole("button", { name: "ALL TOPICS" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "WORK" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "NOTE 1" }).style.getPropertyValue("--point-color")).toBe(
      topicPointHighlightColor,
    );
    expect(screen.getByRole("button", { name: "NOTE 2" }).style.getPropertyValue("--point-color")).toBe(
      topicMutedColor,
    );

    await userEvent.click(screen.getByRole("button", { name: "WORK" }));

    expect(screen.getByRole("button", { name: "ALL TOPICS" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "NOTE 1" }).style.getPropertyValue("--point-color")).toBe(
      topicPointColor,
    );
  });

  it("hover shows the gist tooltip and click opens the raw-note side panel", async () => {
    const fetchMock = installFetchMock();

    await renderAuthenticatedMap();
    const point1 = await screen.findByRole("button", { name: "NOTE 1" });
    await userEvent.hover(point1);

    expect(await screen.findByRole("tooltip")).toHaveTextContent("Built a map prototype.");
    expect(screen.getByRole("tooltip")).toHaveClass("font-gerstnerprogramm");

    await userEvent.click(point1);
    const panel = await screen.findByRole("complementary", { name: "NOTE 1 DETAILS" });

    expect(fetchMock).toHaveBeenCalledWith("/api/notes/1", expect.objectContaining({ credentials: "include" }));
    expect(point1.style.getPropertyValue("--point-color")).toBe(clusterColor(0));
    expect(point1).toHaveClass("outline-ink-black");
    expect(within(panel).getByText("Today I built the first map prototype from my journal embeddings.")).toHaveClass(
      "font-gerstnerprogramm",
    );
    expect(within(panel).getByText("DAY SUMMARY")).toHaveClass("font-ftsystemmono");
    expect(within(panel).getByText("A focused day of prototyping.")).toHaveClass("font-gerstnerprogramm");
    expect(within(panel).getByText("CALM")).toBeInTheDocument();
    expect(within(panel).getByText("The note is reflective and steady.")).toBeInTheDocument();
    expect(within(panel).getByText("WORK")).toBeInTheDocument();
    expect(within(panel).getByText("FOCUS")).toBeInTheDocument();
    expect(within(panel).getByText("0.82")).toBeInTheDocument();
    const openDay = within(panel).getByRole("link", { name: "2026-02-13 · OPEN DAY" });
    expect(openDay).toHaveAttribute("href", "#journal/2026-02-13");
  });

  it("hides the day summary block when the note has no summary", async () => {
    installFetchMock({
      note: {
        ...notePayload,
        day_summary: "",
      },
    });

    await renderAuthenticatedMap();
    await userEvent.click(await screen.findByRole("button", { name: "NOTE 1" }));

    const panel = await screen.findByRole("complementary", { name: "NOTE 1 DETAILS" });
    expect(within(panel).queryByText("DAY SUMMARY")).not.toBeInTheDocument();
  });

  it("renders an empty map state with no note point controls", async () => {
    installFetchMock({
      map: {
        ...mapPayload,
        points: [],
        clusters: [],
        signature: "notes:0:max:0",
      },
    });

    await renderAuthenticatedMap();

    expect(await screen.findByText("NO NOTES TO MAP")).toBeInTheDocument();
    expect(screen.queryAllByRole("button", { name: /^NOTE \d+$/ })).toHaveLength(0);
  });

  it("missing notes show a sanitized unavailable state", async () => {
    installFetchMock({
      note: { detail: "Missing /Users/artem/Vault/2026/02/2026-02-13.md" },
      noteStatus: 404,
    });

    await renderAuthenticatedMap();

    await userEvent.click(await screen.findByRole("button", { name: "NOTE 1" }));
    await waitFor(() => {
      expect(screen.getByText("NOTE UNAVAILABLE")).toBeInTheDocument();
    });
    expect(screen.queryByText("Today I built the first map prototype from my journal embeddings.")).not.toBeInTheDocument();
    expect(screen.queryByText(/Users\/artem/)).not.toBeInTheDocument();
  });
});

describe("Interactive embedding map navigation", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    installMapViewportRect();
    window.location.hash = "";
  });

  afterEach(() => {
    window.location.hash = "";
  });

  it("AC-1: fits widely spaced map coordinates inside the initial visible viewport", async () => {
    installFetchMock({
      map: mapWithPoints([
        { id: 1, x: -1000, y: -1000 },
        { id: 2, x: 0, y: 0 },
        { id: 3, x: 1000, y: 1000 },
      ]),
    });

    await renderAuthenticatedMap();

    expectPointInsideViewport(await screen.findByRole("button", { name: "NOTE 1" }));
    expectPointInsideViewport(screen.getByRole("button", { name: "NOTE 2" }));
    expectPointInsideViewport(screen.getByRole("button", { name: "NOTE 3" }));
  });

  it("AC-2: does not render a dedicated ZOOM IN button", async () => {
    installFetchMock();

    await renderAuthenticatedMap();

    expect(screen.queryByRole("button", { name: "ZOOM IN" })).not.toBeInTheDocument();
  });

  it("AC-3/AC-4: wheel events zoom around the cursor and then zoom back toward the fitted view", async () => {
    installFetchMock();

    await renderAuthenticatedMap();
    const viewport = await screen.findByTestId("map-viewport");
    const initial = readViewportTransform(viewport);

    fireEvent.wheel(viewport, { deltaY: -100, clientX: 320, clientY: 240 });
    const zoomed = readViewportTransform(viewport);
    const expectedZoomed = expectedZoomAt(initial, 320, 240, -100);
    expect(zoomed.scale).toBeGreaterThan(initial.scale);
    expect(zoomed.scale).toBeCloseTo(expectedZoomed.scale, 5);
    expect(zoomed.x).toBeCloseTo(expectedZoomed.x, 5);
    expect(zoomed.y).toBeCloseTo(expectedZoomed.y, 5);

    fireEvent.wheel(viewport, { deltaY: 100, clientX: 320, clientY: 240 });
    const zoomedBack = readViewportTransform(viewport);
    const expectedZoomedBack = expectedZoomAt(zoomed, 320, 240, 100);
    expect(zoomedBack.scale).toBeLessThan(zoomed.scale);
    expect(zoomedBack.scale).toBeCloseTo(expectedZoomedBack.scale, 5);
    expect(zoomedBack.x).toBeCloseTo(expectedZoomedBack.x, 5);
    expect(zoomedBack.y).toBeCloseTo(expectedZoomedBack.y, 5);
  });

  it("AC-5: pointer drag pans the rendered point layer by the cursor delta", async () => {
    installFetchMock();

    await renderAuthenticatedMap();
    const viewport = await screen.findByTestId("map-viewport");
    const before = readViewportTransform(viewport);

    fireEvent.pointerDown(viewport, { pointerId: 1, button: 0, buttons: 1, clientX: 300, clientY: 240 });
    fireEvent.pointerMove(viewport, { pointerId: 1, buttons: 1, clientX: 340, clientY: 270 });
    fireEvent.pointerUp(viewport, { pointerId: 1, button: 0, buttons: 0, clientX: 340, clientY: 270 });

    const after = readViewportTransform(viewport);
    expect(after.x - before.x).toBe(40);
    expect(after.y - before.y).toBe(30);
  });

  it("AC-6: clicking a point still selects the note after zooming and panning", async () => {
    installFetchMock();

    await renderAuthenticatedMap();
    const viewport = await screen.findByTestId("map-viewport");

    fireEvent.wheel(viewport, { deltaY: -100, clientX: 320, clientY: 240 });
    const zoomed = readViewportTransform(viewport);
    fireEvent.pointerDown(viewport, { pointerId: 1, button: 0, buttons: 1, clientX: 300, clientY: 240 });
    fireEvent.pointerMove(viewport, { pointerId: 1, buttons: 1, clientX: 340, clientY: 270 });
    fireEvent.pointerUp(viewport, { pointerId: 1, button: 0, buttons: 0, clientX: 340, clientY: 270 });

    const transformed = readViewportTransform(viewport);
    expect(transformed.scale).toBeGreaterThan(1);
    expect(transformed.x).toBeCloseTo(zoomed.x + 40, 5);
    expect(transformed.y).toBeCloseTo(zoomed.y + 30, 5);

    await userEvent.click(screen.getByRole("button", { name: "NOTE 1" }));

    const panel = await screen.findByRole("complementary", { name: "NOTE 1 DETAILS" });
    expect(within(panel).getByText("Today I built the first map prototype from my journal embeddings.")).toBeInTheDocument();
  });

  it("AC-7: RESET VIEW restores the same fitted positions after zooming and panning", async () => {
    installFetchMock({
      map: mapWithPoints([
        { id: 1, x: -1000, y: -1000 },
        { id: 2, x: 0, y: 0 },
        { id: 3, x: 1000, y: 1000 },
      ]),
    });

    await renderAuthenticatedMap();
    const viewport = await screen.findByTestId("map-viewport");
    const note1 = screen.getByRole("button", { name: "NOTE 1" });
    const initial = readPointCenter(note1);

    fireEvent.wheel(viewport, { deltaY: -100, clientX: 320, clientY: 240 });
    fireEvent.pointerDown(viewport, { pointerId: 1, button: 0, buttons: 1, clientX: 300, clientY: 240 });
    fireEvent.pointerMove(viewport, { pointerId: 1, buttons: 1, clientX: 340, clientY: 270 });
    fireEvent.pointerUp(viewport, { pointerId: 1, button: 0, buttons: 0, clientX: 340, clientY: 270 });

    expect(readViewportTransform(viewport)).not.toEqual({ scale: 1, x: 0, y: 0 });

    await userEvent.click(screen.getByRole("button", { name: "RESET VIEW" }));

    const reset = readPointCenter(note1);
    expect(reset.x).toBeCloseTo(initial.x, 5);
    expect(reset.y).toBeCloseTo(initial.y, 5);
    expect(readViewportTransform(viewport)).toEqual({ scale: 1, x: 0, y: 0 });
  });

  it("EC-1/EC-2/ERR-1: finite points stay drawable when coordinates collapse or one coordinate is invalid", async () => {
    installFetchMock({
      preserveMapObject: true,
      map: mapWithPoints([
        { id: 1, x: 5, y: 5 },
        { id: 2, x: 5, y: 5 },
        { id: 99, x: Number.POSITIVE_INFINITY, y: 42 },
      ]),
    });

    await renderAuthenticatedMap();

    const note1 = await screen.findByRole("button", { name: "NOTE 1" });
    const note2 = screen.getByRole("button", { name: "NOTE 2" });
    const note1Center = readPointCenter(note1);
    const note2Center = readPointCenter(note2);

    expect(note1Center.x).toBeCloseTo(320, 5);
    expect(note1Center.y).toBeCloseTo(240, 5);
    expect(note2Center.x).toBeCloseTo(320, 5);
    expect(note2Center.y).toBeCloseTo(240, 5);
    expectPointInsideViewport(note1);
    expectPointInsideViewport(note2);
    expect(screen.queryByRole("button", { name: "NOTE 99" })).not.toBeInTheDocument();
  });
});

describe("Map interaction regressions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    installMapViewportRect();
    window.location.hash = "";
  });

  afterEach(() => {
    window.location.hash = "";
  });

  it("moves and scales the blueprint grid with the plotted data while still covering the panel", async () => {
    installFetchMock();

    await renderAuthenticatedMap();
    const viewport = await screen.findByTestId("map-viewport");
    const grid = await screen.findByTestId("map-grid");
    const initialBackgroundSize = grid.style.backgroundSize;

    expect(viewport).not.toHaveClass("transition-transform");

    fireEvent.wheel(viewport, { deltaY: -100, clientX: 320, clientY: 240 });
    expect(readViewportTransform(viewport).scale).toBeGreaterThan(1);
    expect(grid.style.backgroundSize).not.toBe(initialBackgroundSize);

    fireEvent.pointerDown(viewport, { pointerId: 1, button: 0, buttons: 1, clientX: 300, clientY: 240 });
    fireEvent.pointerMove(viewport, { pointerId: 1, buttons: 1, clientX: 340, clientY: 270 });
    fireEvent.pointerUp(viewport, { pointerId: 1, button: 0, buttons: 0, clientX: 340, clientY: 270 });

    const afterPan = readViewportTransform(viewport);
    expect(grid.style.backgroundPosition).toBe(`${formatPx(afterPan.x)} ${formatPx(afterPan.y)}`);
    expect(viewport.style.transformOrigin).toBe("0px 0px");
    expect(grid.closest("[data-testid='map-viewport']")).toBeNull();
  });

  it("uses a gentle wheel zoom step so one standard wheel tick changes scale by less than 10%", async () => {
    installFetchMock();

    await renderAuthenticatedMap();
    const viewport = await screen.findByTestId("map-viewport");

    fireEvent.wheel(viewport, { deltaY: -100, clientX: 320, clientY: 240 });

    const zoomed = readViewportTransform(viewport);
    expect(zoomed.scale).toBeGreaterThan(1);
    expect(zoomed.scale).toBeLessThan(1.1);
  });

  it("keeps wheel gestures local to the map instead of bubbling into the page", async () => {
    installFetchMock();
    const windowWheelHandler = vi.fn();
    window.addEventListener("wheel", windowWheelHandler);

    await renderAuthenticatedMap();
    const viewport = await screen.findByTestId("map-viewport");
    const wheelEvent = new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      clientX: 320,
      clientY: 240,
      deltaY: -100,
    });

    fireEvent(viewport, wheelEvent);

    expect(wheelEvent.defaultPrevented).toBe(true);
    expect(windowWheelHandler).not.toHaveBeenCalled();
    expect(readViewportTransform(viewport).scale).toBeGreaterThan(1);

    window.removeEventListener("wheel", windowWheelHandler);
  });

  it("keeps point clicks from starting a drag gesture before opening the note panel", async () => {
    installFetchMock();

    await renderAuthenticatedMap();
    const viewport = await screen.findByTestId("map-viewport");
    const point1 = await screen.findByRole("button", { name: "NOTE 1" });

    fireEvent.pointerDown(point1, { pointerId: 1, button: 0, buttons: 1, clientX: 300, clientY: 240 });
    fireEvent.pointerMove(viewport, { pointerId: 1, buttons: 1, clientX: 306, clientY: 246 });
    fireEvent.pointerUp(point1, { pointerId: 1, button: 0, buttons: 0, clientX: 306, clientY: 246 });

    expect(readViewportTransform(viewport)).toEqual({ scale: 1, x: 0, y: 0 });

    await userEvent.click(point1);

    expect(await screen.findByRole("complementary", { name: "NOTE 1 DETAILS" })).toBeInTheDocument();
  });

  it("recolors points by mood and selected topic after zooming and panning the data layer", async () => {
    installFetchMock();

    await renderAuthenticatedMap();
    const viewport = await screen.findByTestId("map-viewport");

    fireEvent.wheel(viewport, { deltaY: -100, clientX: 320, clientY: 240 });
    fireEvent.pointerDown(viewport, { pointerId: 1, button: 0, buttons: 1, clientX: 300, clientY: 240 });
    fireEvent.pointerMove(viewport, { pointerId: 1, buttons: 1, clientX: 340, clientY: 270 });
    fireEvent.pointerUp(viewport, { pointerId: 1, button: 0, buttons: 0, clientX: 340, clientY: 270 });

    await userEvent.click(screen.getByRole("button", { name: "MOOD" }));
    expect(screen.getByLabelText("MOOD LEGEND")).toBeInTheDocument();
    expect(screen.getByTestId("legend-swatch-mood-calm").style.getPropertyValue("--legend-color")).toBe(
      moodPalette.calm,
    );
    expect(screen.getByRole("button", { name: "NOTE 1" }).style.getPropertyValue("--point-color")).toBe(
      moodPalette.calm,
    );
    expect(screen.getByRole("button", { name: "NOTE 2" }).style.getPropertyValue("--point-color")).toBe(
      moodPalette.joy,
    );

    await userEvent.click(screen.getByRole("button", { name: "TOPIC" }));
    await userEvent.click(screen.getByRole("button", { name: "WORK" }));
    expect(screen.getByRole("button", { name: "NOTE 1" }).style.getPropertyValue("--point-color")).toBe(
      topicPointHighlightColor,
    );
    expect(screen.getByRole("button", { name: "NOTE 2" }).style.getPropertyValue("--point-color")).toBe(
      topicMutedColor,
    );
  });

  it("keeps a long selected note inside a scrollable side panel without stretching the map", async () => {
    installFetchMock({
      note: {
        ...notePayload,
        raw_text: Array.from({ length: 24 }, (_, index) => `Long note paragraph ${index + 1}.`).join("\n\n"),
      },
    });

    await renderAuthenticatedMap();
    await userEvent.click(await screen.findByRole("button", { name: "NOTE 1" }));

    const mapPanel = await screen.findByTestId("map-panel");
    const notePanel = await screen.findByRole("complementary", { name: "NOTE 1 DETAILS" });
    const rawText = within(notePanel).getByText(/Long note paragraph 24/);

    expect(mapPanel).toHaveClass("h-[min(62vh,560px)]");
    expect(notePanel).toHaveClass("max-h-[min(62vh,560px)]");
    expect(notePanel).toHaveClass("overflow-y-auto");
    expect(rawText).toHaveClass("whitespace-pre-wrap");
  });
});
