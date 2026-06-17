export type MapPoint = {
  id: number;
  x: number;
  y: number;
  cluster_id: number;
  mood: "joy" | "calm" | "sadness" | "anger" | "fear" | "neutral" | "mixed";
  topics: string[];
  gist: string;
  date: string;
  ts: string;
};

export type MapCluster = {
  id: number;
  label: string;
  size: number;
  dominant_topics: string[];
};

export type MapPayload = {
  signature: string;
  computed_at: string;
  points: MapPoint[];
  clusters: MapCluster[];
};

export type NoteDetails = {
  id: number;
  date: string;
  ts: string;
  mood: MapPoint["mood"];
  mood_confidence: number;
  mood_evidence: string;
  topics: string[];
  gist: string;
  raw_text: string;
  day_summary: string;
  note_path: string;
};

export async function fetchMap(): Promise<MapPayload> {
  const response = await fetch("/api/map", {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error("MAP UNAVAILABLE");
  }
  return response.json() as Promise<MapPayload>;
}

export async function fetchNoteDetails(id: number): Promise<NoteDetails> {
  const response = await fetch(`/api/notes/${id}`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error("NOTE UNAVAILABLE");
  }
  return response.json() as Promise<NoteDetails>;
}
