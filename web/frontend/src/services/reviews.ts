export type ReviewKind = "week" | "month";

export type ReviewArchiveItem = {
  kind: ReviewKind;
  period: string;
  start_date: string;
  end_date: string;
  title: string;
  counts: { entry_count?: number; active_days?: number };
  has_image: boolean;
  language: string;
  version: number;
  updated_at: string | null;
};

export type ReviewEvidence = {
  id: string;
  type: "diary" | "review" | "vault";
  label: string;
  href: string | null;
};

export type ReviewDetail = {
  kind: ReviewKind;
  period: string;
  start_date: string;
  end_date: string;
  title: string;
  paragraphs: Array<{ text: string; evidence: ReviewEvidence[] }>;
  reflection_question: string;
  safety_note: string | null;
  counts: { entry_count?: number; active_days?: number };
  image: { url: string; alt: string } | null;
  language: string;
  model: string;
  version: number;
  created_at: string | null;
  updated_at: string | null;
};

export type ReviewJob = {
  job_id: number;
  status: "pending" | "running" | "complete" | "failed" | "superseded";
};

export type ReviewCapabilities = {
  regenerate: boolean;
};

export async function fetchReviewCapabilities(): Promise<ReviewCapabilities> {
  return fetchJson("/api/reviews/capabilities");
}

export async function fetchReviewArchive(
  kind: ReviewKind,
): Promise<{ reviews: ReviewArchiveItem[] }> {
  return fetchJson(`/api/reviews?kind=${encodeURIComponent(kind)}`);
}

export async function fetchReview(
  kind: ReviewKind,
  period: string,
): Promise<ReviewDetail> {
  return fetchJson(`/api/reviews/${kind}/${encodeURIComponent(period)}`);
}

export async function regenerateReview(
  kind: ReviewKind,
  period: string,
  signal?: AbortSignal,
): Promise<ReviewJob> {
  return fetchJson(`/api/reviews/${kind}/${encodeURIComponent(period)}/regenerate`, {
    method: "POST",
    signal,
  });
}

export async function fetchReviewJob(
  jobId: number,
  signal?: AbortSignal,
): Promise<ReviewJob> {
  return fetchJson(`/api/review-jobs/${jobId}`, { signal });
}

async function fetchJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { ...init, credentials: "include" });
  if (!response.ok) {
    throw new Error("REVIEW UNAVAILABLE");
  }
  return response.json() as Promise<T>;
}
