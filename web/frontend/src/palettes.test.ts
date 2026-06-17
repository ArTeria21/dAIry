import { describe, expect, it } from "vitest";

import {
  clusterPalette,
  moodColor,
  moodPalette,
  topicColor,
  topicHighlightColor,
  topicMutedColor,
  topicPalette,
} from "./design/palettes";

describe("Phase 2 shared visualization palettes", () => {
  it("AC-3: defines one shared muted color for each supported mood", () => {
    expect(Object.keys(moodPalette).sort()).toEqual([
      "anger",
      "calm",
      "fear",
      "joy",
      "mixed",
      "neutral",
      "sadness",
    ]);
    expect(moodColor("calm")).toBe(moodPalette.calm);
    expect(moodColor("sadness")).toBe(moodPalette.sadness);
    expect(new Set(Object.values(moodPalette)).size).toBe(7);
    expect(Object.values(moodPalette)).not.toContain("#fb631b");
  });

  it("AC-3: keeps clusters muted and topics on a stable category palette", () => {
    expect(clusterPalette.length).toBeGreaterThanOrEqual(8);
    expect(clusterPalette).not.toContain("#fb631b");
    expect(topicPalette.length).toBeGreaterThanOrEqual(10);
    expect(topicPalette).not.toContain(topicHighlightColor);
    expect(topicPalette).toContain(topicColor("work"));
    expect(topicColor("work")).not.toBe(topicMutedColor);
    expect(topicColor("")).toBe(topicMutedColor);
    expect(topicHighlightColor).toBe("#fb631b");
    expect(topicMutedColor).toBe("#858483");
  });
});
