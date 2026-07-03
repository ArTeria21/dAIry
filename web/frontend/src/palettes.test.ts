import { describe, expect, it } from "vitest";

import {
  clusterColor,
  clusterPalette,
  moodColor,
  moodPalette,
  noiseColor,
  topicDimmedColor,
  topicMutedColor,
  topicPointColor,
  topicPointHighlightColor,
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

  it("AC-3: keeps clusters muted and reserves topic colors for highlight states", () => {
    expect(clusterPalette).toHaveLength(8);
    expect(clusterPalette).not.toContain("#fb631b");
    expect(clusterColor(-1)).toBe(noiseColor);
    expect(clusterColor(0)).toBe(clusterPalette[0]);
    expect(clusterColor(8)).toBe(clusterPalette[0]);
    expect(noiseColor).toBe("#cbcbcb");
    expect(topicPointColor).toBe("#858483");
    expect(topicPointHighlightColor).toBe("#181818");
    expect(topicMutedColor).toBe("#858483");
    expect(topicDimmedColor).toBe("#dedede");
  });
});
