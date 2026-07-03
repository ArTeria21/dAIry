import { describe, expect, it } from "vitest";

import { panBy, zoomAt, type ViewTransform } from "./viewTransform";

const opts = {
  minZoom: 0.5,
  maxZoom: 6,
  intensity: 0.0008,
};

function worldPoint(transform: ViewTransform, cursorX: number, cursorY: number) {
  return {
    x: (cursorX - transform.x) / transform.scale,
    y: (cursorY - transform.y) / transform.scale,
  };
}

describe("map view transforms", () => {
  it("keeps the world point under the cursor fixed while zooming", () => {
    const current = { scale: 1.7, x: -38, y: 24 };
    const cursor = { x: 412, y: 156 };
    const before = worldPoint(current, cursor.x, cursor.y);

    const next = zoomAt(current, cursor.x, cursor.y, -135, opts);
    const after = worldPoint(next, cursor.x, cursor.y);

    expect(after.x).toBeCloseTo(before.x, 10);
    expect(after.y).toBeCloseTo(before.y, 10);
    expect(next.scale).toBeGreaterThan(current.scale);
  });

  it("clamps zoom deltas and scale to the configured bounds", () => {
    const current = { scale: 1, x: 0, y: 0 };
    const largeZoomIn = zoomAt(current, 320, 240, -9999, opts);
    const clampedZoomIn = zoomAt(current, 320, 240, -240, opts);
    const largeZoomOut = zoomAt(current, 320, 240, 9999, opts);
    const clampedZoomOut = zoomAt(current, 320, 240, 240, opts);

    expect(largeZoomIn).toEqual(clampedZoomIn);
    expect(largeZoomOut).toEqual(clampedZoomOut);
    expect(zoomAt({ scale: 5.99, x: 10, y: 20 }, 320, 240, -240, opts).scale).toBe(6);
    expect(zoomAt({ scale: 0.51, x: 10, y: 20 }, 320, 240, 240, opts).scale).toBe(0.5);
  });

  it("does not drift at min or max zoom", () => {
    const atMax = { scale: 6, x: -123, y: 45 };
    const atMin = { scale: 0.5, x: 19, y: -77 };

    expect(zoomAt(atMax, 320, 240, -120, opts)).toEqual(atMax);
    expect(zoomAt(atMin, 320, 240, 120, opts)).toEqual(atMin);
  });

  it("pans by screen-pixel deltas without changing scale", () => {
    expect(panBy({ scale: 2, x: 10, y: -4 }, 30, -15)).toEqual({
      scale: 2,
      x: 40,
      y: -19,
    });
  });
});
