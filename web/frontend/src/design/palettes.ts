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
  "#214f6b",
  "#577184",
  "#476967",
  "#6f7666",
  "#7a6e79",
  "#5f6070",
  "#7c7a72",
  "#486078",
  "#687f7a",
  "#626b58",
];

export const topicHighlightColor = "#fb631b";
export const topicMutedColor = "#858483";

export const topicPalette = [
  "#2563a6",
  "#c76a1d",
  "#2f7d5c",
  "#8b5aa9",
  "#b3474b",
  "#9a7a16",
  "#0f766e",
  "#6d6f31",
  "#a45185",
  "#5f6f8f",
  "#7a5c40",
  "#3b7a99",
];

export function moodColor(mood: Mood): string {
  return moodPalette[mood];
}

export function topicColor(topic: string): string {
  if (!topic) {
    return topicMutedColor;
  }

  return topicPalette[Math.abs(hashString(topic)) % topicPalette.length];
}

function hashString(value: string): number {
  let hash = 0;
  for (const character of value) {
    hash = (hash * 31 + character.charCodeAt(0)) | 0;
  }
  return hash;
}
