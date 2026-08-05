export type MapPoint = {
  id: string;
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
  description?: string;
};

export type MapPayload = {
  signature: string;
  computed_at: string;
  n_noise: number;
  points: MapPoint[];
  clusters: MapCluster[];
};

export type NoteDetails = {
  id: string;
  date: string;
  ts: string;
  mood: MapPoint["mood"];
  mood_confidence: number;
  mood_evidence: string;
  topics: string[];
  gist: string;
  raw_text: string;
  raw_text_sha256: string;
  day_summary: string | null;
  note_path: string;
};

export class SemanticIndexBuildingError extends Error {
  constructor() {
    super("SEMANTIC INDEX IS BEING BUILT");
    this.name = "SemanticIndexBuildingError";
  }
}

export async function fetchMap(): Promise<MapPayload> {
  const response = await fetch("/api/map", {
    credentials: "include",
  });
  if (!response.ok) {
    if (response.status === 503) {
      const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
      if (body?.detail === "semantic_index_building") {
        throw new SemanticIndexBuildingError();
      }
    }
    throw new Error("MAP UNAVAILABLE");
  }
  return response.json() as Promise<MapPayload>;
}

export async function fetchNoteDetails(id: string): Promise<NoteDetails> {
  const response = await fetch(`/api/notes/${encodeURIComponent(id)}`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error("NOTE UNAVAILABLE");
  }
  return response.json() as Promise<NoteDetails>;
}
