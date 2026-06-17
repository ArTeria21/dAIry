from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Protocol

from dairy_web.data_access import NoteRecord


@dataclass(frozen=True, slots=True)
class MapPoint:
    id: str
    x: float
    y: float
    cluster_id: int
    mood: str
    topics: list[str]
    gist: str
    date: str
    ts: str


@dataclass(frozen=True, slots=True)
class ClusterSummary:
    id: int
    label: str
    size: int
    dominant_topics: list[str]


@dataclass(frozen=True, slots=True)
class MapSnapshot:
    signature: str
    computed_at: str
    points: list[MapPoint]
    clusters: list[ClusterSummary]


@dataclass(frozen=True, slots=True)
class RebuildResult:
    signature: str
    computed_at: str
    n_points: int
    n_clusters: int


class NoteStore(Protocol):
    def list_notes(self) -> list[NoteRecord]: ...


class Projector(Protocol):
    def project(self, vectors: list[list[float]]) -> list[tuple[float, float]]: ...


class Clusterer(Protocol):
    def cluster(self, vectors: list[list[float]]) -> list[int]: ...


class ClusterLabeler(Protocol):
    def label_clusters(self, clusters: dict[int, list[NoteRecord]]) -> dict[int, str]: ...


class AnalysisCache:
    """Read-write cache owned by Layer 3 for derived projection artifacts."""

    def __init__(self, cache_path: Path | str) -> None:
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def load_snapshot(self, signature: str) -> MapSnapshot | None:
        with self._connect() as conn:
            meta = conn.execute(
                "SELECT signature, computed_at FROM metadata WHERE id = 1"
            ).fetchone()
            if meta is None or meta["signature"] != signature:
                return None
            point_rows = conn.execute(
                "SELECT * FROM points ORDER BY note_id"
            ).fetchall()
            cluster_rows = conn.execute(
                "SELECT * FROM clusters ORDER BY cluster_id"
            ).fetchall()
        return MapSnapshot(
            signature=str(meta["signature"]),
            computed_at=str(meta["computed_at"]),
            points=[_point_from_row(row) for row in point_rows],
            clusters=[_cluster_from_row(row) for row in cluster_rows],
        )

    def save_snapshot(self, snapshot: MapSnapshot) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM points")
            conn.execute("DELETE FROM clusters")
            conn.execute("DELETE FROM metadata")
            conn.execute(
                """
                INSERT INTO metadata (id, signature, computed_at)
                VALUES (1, ?, ?)
                """,
                (snapshot.signature, snapshot.computed_at),
            )
            conn.executemany(
                """
                INSERT INTO points (
                    note_id, x, y, cluster_id, mood, topics_json, gist, date, ts
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        point.id,
                        point.x,
                        point.y,
                        point.cluster_id,
                        point.mood,
                        json.dumps(point.topics, ensure_ascii=False),
                        point.gist,
                        point.date,
                        point.ts,
                    )
                    for point in snapshot.points
                ],
            )
            conn.executemany(
                """
                INSERT INTO clusters (
                    cluster_id, label, size, dominant_topics_json
                )
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        cluster.id,
                        cluster.label,
                        cluster.size,
                        json.dumps(cluster.dominant_topics, ensure_ascii=False),
                    )
                    for cluster in snapshot.clusters
                ],
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.cache_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    signature TEXT NOT NULL,
                    computed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS points (
                    note_id TEXT PRIMARY KEY,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    cluster_id INTEGER NOT NULL,
                    mood TEXT NOT NULL,
                    topics_json TEXT NOT NULL,
                    gist TEXT NOT NULL,
                    date TEXT NOT NULL,
                    ts TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS clusters (
                    cluster_id INTEGER PRIMARY KEY,
                    label TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    dominant_topics_json TEXT NOT NULL
                );
                """
            )


class AnalysisService:
    def __init__(
        self,
        *,
        store: NoteStore,
        cache: AnalysisCache,
        projector: Projector | None = None,
        clusterer: Clusterer | None = None,
        labeler: ClusterLabeler | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.cache = cache
        self.projector = projector or UmapProjector()
        self.clusterer = clusterer or HdbscanClusterer()
        self.labeler = labeler or StaticClusterLabeler()
        self.now = now or (lambda: datetime.now(timezone.utc))

    def get_map(self) -> MapSnapshot:
        notes = self.store.list_notes()
        signature = note_set_signature(notes)
        cached = self.cache.load_snapshot(signature)
        if cached is not None:
            return cached
        return self._recompute(notes, signature)

    def rebuild(self) -> RebuildResult:
        notes = self.store.list_notes()
        snapshot = self._recompute(notes, note_set_signature(notes))
        return RebuildResult(
            signature=snapshot.signature,
            computed_at=snapshot.computed_at,
            n_points=len(snapshot.points),
            n_clusters=len(snapshot.clusters),
        )

    def _recompute(self, notes: list[NoteRecord], signature: str) -> MapSnapshot:
        computed_at = self.now().astimezone(timezone.utc).isoformat()
        if not notes:
            snapshot = MapSnapshot(
                signature=signature,
                computed_at=computed_at,
                points=[],
                clusters=[],
            )
            self.cache.save_snapshot(snapshot)
            return snapshot

        vectors = _embedding_matrix(notes)
        coordinates = self.projector.project(vectors)
        labels = self.clusterer.cluster(vectors)
        if len(coordinates) != len(notes) or len(labels) != len(notes):
            raise ValueError("Projection and cluster lengths must match note count")

        clusters_by_id = _clusters_by_id(notes, labels)
        cluster_labels = (
            self.labeler.label_clusters(clusters_by_id) if clusters_by_id else {}
        )
        points = [
            MapPoint(
                id=note.id,
                x=float(coordinates[index][0]),
                y=float(coordinates[index][1]),
                cluster_id=int(labels[index]),
                mood=note.mood,
                topics=list(note.topics),
                gist=note.gist,
                date=note.date,
                ts=note.ts,
            )
            for index, note in enumerate(notes)
        ]
        clusters = [
            ClusterSummary(
                id=cluster_id,
                label=cluster_labels.get(cluster_id, f"Cluster {cluster_id}"),
                size=len(cluster_notes),
                dominant_topics=_dominant_topics(cluster_notes),
            )
            for cluster_id, cluster_notes in sorted(clusters_by_id.items())
        ]
        snapshot = MapSnapshot(
            signature=signature,
            computed_at=computed_at,
            points=points,
            clusters=clusters,
        )
        self.cache.save_snapshot(snapshot)
        return snapshot


class UmapProjector:
    def project(self, vectors: list[list[float]]) -> list[tuple[float, float]]:
        import umap

        model = umap.UMAP(n_components=2, random_state=42)
        coordinates = model.fit_transform(vectors)
        return [(float(x), float(y)) for x, y in coordinates]


class HdbscanClusterer:
    def cluster(self, vectors: list[list[float]]) -> list[int]:
        import hdbscan

        min_cluster_size = max(2, min(8, len(vectors)))
        model = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size)
        return [int(label) for label in model.fit_predict(vectors)]


class StaticClusterLabeler:
    def label_clusters(self, clusters: dict[int, list[NoteRecord]]) -> dict[int, str]:
        return {
            cluster_id: " / ".join(_dominant_topics(notes)[:2]) or f"Cluster {cluster_id}"
            for cluster_id, notes in clusters.items()
        }


class OpenRouterClusterLabeler:
    def __init__(
        self,
        *,
        model_name: str,
        api_key: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        complete: Callable[[str, str], str] | None = None,
        max_gists: int = 8,
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.complete = complete or self._complete_with_openrouter
        self.max_gists = max_gists

    def label_clusters(self, clusters: dict[int, list[NoteRecord]]) -> dict[int, str]:
        return {
            cluster_id: _clean_cluster_label(
                self.complete(self.model_name, self._prompt(cluster_notes))
            )
            for cluster_id, cluster_notes in clusters.items()
        }

    def _prompt(self, notes: list[NoteRecord]) -> str:
        sample = notes[: self.max_gists]
        gists = "\n".join(f"- {note.gist}" for note in sample)
        return f"Name this journal cluster in 2-4 words.\nGists:\n{gists}"

    def _complete_with_openrouter(self, model: str, prompt: str) -> str:
        from openai import OpenAI

        api_key = self.api_key or os.environ["OPENROUTER_API_KEY"]
        client = OpenAI(base_url=self.base_url, api_key=api_key)
        completion = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Return only a concise 2-4 word English label for this "
                        "personal journal cluster."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        return completion.choices[0].message.content or "Cluster"


def note_set_signature(notes: Iterable[NoteRecord]) -> str:
    ids = sorted(note.id for note in notes)
    ids_hash = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    max_id = max(ids) if ids else ""
    return f"count={len(ids)};max={max_id};ids={ids_hash}"


def _embedding_matrix(notes: list[NoteRecord]) -> list[list[float]]:
    dimensions = {len(note.embedding) for note in notes}
    if len(dimensions) > 1:
        raise ValueError("All notes must have matching embedding dimensions")
    return [list(note.embedding) for note in notes]


def _clusters_by_id(
    notes: list[NoteRecord], labels: list[int]
) -> dict[int, list[NoteRecord]]:
    clusters: dict[int, list[NoteRecord]] = defaultdict(list)
    for note, label in zip(notes, labels, strict=True):
        if int(label) == -1:
            continue
        clusters[int(label)].append(note)
    return dict(clusters)


def _dominant_topics(notes: list[NoteRecord]) -> list[str]:
    counts = Counter(topic for note in notes for topic in note.topics)
    return [topic for topic, _ in counts.most_common(3)]


def _point_from_row(row: sqlite3.Row) -> MapPoint:
    return MapPoint(
        id=str(row["note_id"]),
        x=float(row["x"]),
        y=float(row["y"]),
        cluster_id=int(row["cluster_id"]),
        mood=str(row["mood"]),
        topics=[str(topic) for topic in json.loads(row["topics_json"])],
        gist=str(row["gist"]),
        date=str(row["date"]),
        ts=str(row["ts"]),
    )


def _cluster_from_row(row: sqlite3.Row) -> ClusterSummary:
    return ClusterSummary(
        id=int(row["cluster_id"]),
        label=str(row["label"]),
        size=int(row["size"]),
        dominant_topics=[
            str(topic) for topic in json.loads(row["dominant_topics_json"])
        ],
    )


def _clean_cluster_label(raw: str) -> str:
    label = " ".join(raw.replace("\n", " ").split())
    label = label.strip(" \"'`.,:;")
    words = label.split()
    if len(words) > 4:
        label = " ".join(words[:4])
    return label or "Cluster"
