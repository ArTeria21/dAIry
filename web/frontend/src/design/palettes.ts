export type Mood =
  | "joy"
  | "calm"
  | "sadness"
  | "anger"
  | "fear"
  | "neutral"
  | "mixed";

export const moodPalette: Record<Mood, string> = {
  joy: "#c28b00",
  calm: "#2f8f83",
  sadness: "#4c78a8",
  anger: "#c44e52",
  fear: "#7b5aa6",
  neutral: "#8c8a83",
  mixed: "#5f6f8f",
};

export const clusterPalette = [
  "#4e79a7",
  "#8a9a5b",
  "#b07aa1",
  "#c2843c",
  "#5f9e9c",
  "#a05d56",
  "#7b6f9e",
  "#6b7b8c",
];

export const noiseColor = "#cbcbcb";
export const topicPointColor = "#858483";
export const topicPointHighlightColor = "#181818";
export const topicMutedColor = "#858483";
export const topicDimmedColor = "#dedede";

export function moodColor(mood: Mood): string {
  return moodPalette[mood];
}

export function clusterColor(clusterId: number): string {
  if (clusterId === -1) {
    return noiseColor;
  }

  return clusterPalette[Math.abs(clusterId) % clusterPalette.length];
}
