from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Protocol

from dairy_bot.prompts import load_prompt
from dairy_web.data_access import NoteRecord


LOGGER = logging.getLogger(__name__)

CLUSTER_REDUCTION_DIMENSIONS = 10
CLUSTER_REDUCTION_NEIGHBORS = 15
MIN_NOTES_FOR_PROJECTION = 5
MIN_NOTES_FOR_CLUSTERING = 15
OPENROUTER_LABEL_TIMEOUT_SECONDS = 20.0
# Revisit these once the corpus grows beyond roughly 1000 notes.
HDBSCAN_MIN_CLUSTER_SIZE = 6
HDBSCAN_MIN_SAMPLES = 3
HDBSCAN_CLUSTER_SELECTION_METHOD = "eom"
LANGUAGE_DISPLAY_NAMES = {"EN": "English", "RU": "Russian"}


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
    description: str = ""


@dataclass(frozen=True, slots=True)
class MapSnapshot:
    signature: str
    computed_at: str
    points: list[MapPoint]
    clusters: list[ClusterSummary]
    n_noise: int


@dataclass(frozen=True, slots=True)
class RebuildResult:
    signature: str
    computed_at: str
    n_points: int
    n_clusters: int
    n_noise: int


class NoteStore(Protocol):
    def list_notes(self) -> list[NoteRecord]: ...

    def note_content_hashes(self) -> dict[str, str]: ...


class Projector(Protocol):
    def project(self, vectors: list[list[float]]) -> list[tuple[float, float]]: ...


class Reducer(Protocol):
    def reduce(self, vectors: list[list[float]]) -> list[list[float]]: ...


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
                "SELECT signature, computed_at, n_noise FROM metadata WHERE id = 1"
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
            n_noise=int(meta["n_noise"]),
        )

    def save_snapshot(self, snapshot: MapSnapshot) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM points")
            conn.execute("DELETE FROM clusters")
            conn.execute("DELETE FROM metadata")
            conn.execute(
                """
                INSERT INTO metadata (id, signature, computed_at, n_noise)
                VALUES (1, ?, ?, ?)
                """,
                (snapshot.signature, snapshot.computed_at, snapshot.n_noise),
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
                    cluster_id, label, size, dominant_topics_json, description
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        cluster.id,
                        cluster.label,
                        cluster.size,
                        json.dumps(cluster.dominant_topics, ensure_ascii=False),
                        cluster.description,
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
            if self._metadata_schema_is_stale(conn):
                conn.executescript(
                    """
                    DROP TABLE IF EXISTS points;
                    DROP TABLE IF EXISTS clusters;
                    DROP TABLE IF EXISTS metadata;
                    """
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    signature TEXT NOT NULL,
                    computed_at TEXT NOT NULL,
                    n_noise INTEGER NOT NULL
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
                    dominant_topics_json TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def _metadata_schema_is_stale(self, conn: sqlite3.Connection) -> bool:
        metadata_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(metadata)")
        }
        if metadata_columns and "n_noise" not in metadata_columns:
            return True
        cluster_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(clusters)")
        }
        return bool(cluster_columns) and "description" not in cluster_columns


class AnalysisService:
    def __init__(
        self,
        *,
        store: NoteStore,
        cache: AnalysisCache,
        projector: Projector | None = None,
        reducer: Reducer | None = None,
        clusterer: Clusterer | None = None,
        labeler: ClusterLabeler | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.cache = cache
        self.projector = projector or UmapProjector()
        self.reducer = reducer or UmapReducer()
        self.clusterer = clusterer or HdbscanClusterer()
        self.labeler = labeler or StaticClusterLabeler()
        self.now = now or (lambda: datetime.now(timezone.utc))

    def get_map(self) -> MapSnapshot:
        notes = self.store.list_notes()
        signature = self._signature(notes)
        cached = self.cache.load_snapshot(signature)
        if cached is not None:
            return cached
        return self._recompute(notes, signature)

    def rebuild(self) -> RebuildResult:
        notes = self.store.list_notes()
        snapshot = self._recompute(
            notes, self._signature(notes)
        )
        return RebuildResult(
            signature=snapshot.signature,
            computed_at=snapshot.computed_at,
            n_points=len(snapshot.points),
            n_clusters=len(snapshot.clusters),
            n_noise=snapshot.n_noise,
        )

    def _signature(self, notes: list[NoteRecord]) -> str:
        hashes = self.store.note_content_hashes()
        semantic_signature = getattr(self.store, "semantic_signature", None)
        if callable(semantic_signature):
            semantic = str(semantic_signature())
            hashes = {
                note.id: f"{hashes.get(note.id, '')}|semantic={semantic}"
                for note in notes
            }
        return note_set_signature(notes, hashes)

    def _recompute(self, notes: list[NoteRecord], signature: str) -> MapSnapshot:
        computed_at = self.now().astimezone(timezone.utc).isoformat()
        if not notes:
            snapshot = MapSnapshot(
                signature=signature,
                computed_at=computed_at,
                points=[],
                clusters=[],
                n_noise=0,
            )
            self.cache.save_snapshot(snapshot)
            return snapshot

        vectors = _embedding_matrix(notes)
        if len(notes) < MIN_NOTES_FOR_CLUSTERING:
            coordinates = self._project_coordinates(notes, vectors)
            labels = [-1] * len(notes)
        else:
            reduced_vectors = self.reducer.reduce(vectors)
            labels = self.clusterer.cluster(reduced_vectors)
            # Project from raw embeddings: the reduced vectors use min_dist=0.0,
            # which collapses points into clumps and is only suitable for HDBSCAN.
            coordinates = self._project_coordinates(notes, vectors)
        if len(coordinates) != len(notes) or len(labels) != len(notes):
            raise ValueError("Projection and cluster lengths must match note count")

        n_noise = sum(1 for label in labels if int(label) == -1)
        clusters_by_id = _clusters_by_id(notes, labels)
        cluster_labels = _label_clusters_with_fallback(self.labeler, clusters_by_id)
        cluster_descriptions = _describe_clusters_with_fallback(
            self.labeler, clusters_by_id
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
                description=cluster_descriptions.get(cluster_id, ""),
            )
            for cluster_id, cluster_notes in sorted(clusters_by_id.items())
        ]
        snapshot = MapSnapshot(
            signature=signature,
            computed_at=computed_at,
            points=points,
            clusters=clusters,
            n_noise=n_noise,
        )
        self.cache.save_snapshot(snapshot)
        return snapshot

    def _project_coordinates(
        self,
        notes: list[NoteRecord],
        vectors: list[list[float]],
    ) -> list[tuple[float, float]]:
        if len(notes) < MIN_NOTES_FOR_PROJECTION:
            return _deterministic_coordinates(len(notes))
        try:
            coordinates = self.projector.project(vectors)
            if len(coordinates) != len(notes):
                raise ValueError("Projection length must match note count")
            return coordinates
        except Exception:
            LOGGER.warning("Projection failed; using deterministic fallback", exc_info=True)
            return _deterministic_coordinates(len(notes))


class UmapProjector:
    def project(self, vectors: list[list[float]]) -> list[tuple[float, float]]:
        import umap

        model = umap.UMAP(n_components=2, metric="cosine", random_state=42)
        coordinates = model.fit_transform(vectors)
        return [(float(x), float(y)) for x, y in coordinates]


class UmapReducer:
    def reduce(self, vectors: list[list[float]]) -> list[list[float]]:
        import umap

        model = umap.UMAP(
            n_components=CLUSTER_REDUCTION_DIMENSIONS,
            n_neighbors=CLUSTER_REDUCTION_NEIGHBORS,
            min_dist=0.0,
            metric="cosine",
            random_state=42,
        )
        reduced = model.fit_transform(vectors)
        return [[float(value) for value in row] for row in reduced]


class HdbscanClusterer:
    def cluster(self, vectors: list[list[float]]) -> list[int]:
        import hdbscan

        model = hdbscan.HDBSCAN(
            min_cluster_size=HDBSCAN_MIN_CLUSTER_SIZE,
            min_samples=HDBSCAN_MIN_SAMPLES,
            cluster_selection_method=HDBSCAN_CLUSTER_SELECTION_METHOD,
        )
        return [int(label) for label in model.fit_predict(vectors)]


class StaticClusterLabeler:
    def label_clusters(self, clusters: dict[int, list[NoteRecord]]) -> dict[int, str]:
        return {
            cluster_id: " / ".join(_dominant_topics(notes)[:2])
            or f"Cluster {cluster_id}"
            for cluster_id, notes in clusters.items()
        }

    def describe_clusters(
        self, clusters: dict[int, list[NoteRecord]]
    ) -> dict[int, str]:
        return {
            cluster_id: _static_cluster_description(notes)
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
        timeout: float = OPENROUTER_LABEL_TIMEOUT_SECONDS,
        language: str = "EN",
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.complete = complete or self._complete_with_openrouter
        self.timeout = timeout
        self.language_name = LANGUAGE_DISPLAY_NAMES.get(
            language.upper(), LANGUAGE_DISPLAY_NAMES["EN"]
        )

    def label_clusters(self, clusters: dict[int, list[NoteRecord]]) -> dict[int, str]:
        fallback_labels = StaticClusterLabeler().label_clusters(clusters)
        labels: dict[int, str] = {}
        for cluster_id, cluster_notes in clusters.items():
            try:
                labels[cluster_id] = self.complete(
                    self.model_name,
                    self._prompt(cluster_notes),
                )
            except Exception:
                LOGGER.warning(
                    "OpenRouter cluster label failed for cluster %s; using static label",
                    cluster_id,
                    exc_info=True,
                )
                labels[cluster_id] = fallback_labels.get(
                    cluster_id, f"Cluster {cluster_id}"
                )
        return labels

    def describe_clusters(
        self, clusters: dict[int, list[NoteRecord]]
    ) -> dict[int, str]:
        descriptions: dict[int, str] = {}
        for cluster_id, cluster_notes in clusters.items():
            try:
                raw = self.complete(
                    self.model_name, self._description_prompt(cluster_notes)
                )
                descriptions[cluster_id] = raw
            except Exception:
                LOGGER.warning(
                    "OpenRouter cluster description failed for cluster %s; "
                    "using static description",
                    cluster_id,
                    exc_info=True,
                )
                descriptions[cluster_id] = _static_cluster_description(cluster_notes)
        return descriptions

    def _prompt(self, notes: list[NoteRecord]) -> str:
        gists = "\n".join(f"- {note.gist}" for note in notes)
        return load_prompt(
            "clusters/label_user",
            output_language=self.language_name,
            gists=gists,
        )

    def _description_prompt(self, notes: list[NoteRecord]) -> str:
        gists = "\n".join(f"- {note.gist}" for note in notes)
        return load_prompt(
            "clusters/description_user",
            output_language=self.language_name,
            gists=gists,
        )

    def _complete_with_openrouter(self, model: str, prompt: str) -> str:
        from openai import OpenAI

        api_key = self.api_key or os.environ["OPENROUTER_API_KEY"]
        client = OpenAI(base_url=self.base_url, api_key=api_key, timeout=self.timeout)
        completion = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": load_prompt(
                        "clusters/system",
                        output_language=self.language_name,
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = completion.choices[0].message.content
        return "" if content is None else content


def note_set_signature(
    notes: Iterable[NoteRecord], content_hashes: dict[str, str] | None = None
) -> str:
    ids = sorted(note.id for note in notes)
    max_id = max(ids) if ids else ""
    if not content_hashes:
        ids_hash = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
        return f"count={len(ids)};max={max_id};ids={ids_hash}"
    state = "|".join(f"{note_id}:{content_hashes.get(note_id, '')}" for note_id in ids)
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    return f"count={len(ids)};max={max_id};state={state_hash}"


def _embedding_matrix(notes: list[NoteRecord]) -> list[list[float]]:
    dimensions = {len(note.embedding) for note in notes}
    if len(dimensions) > 1:
        raise ValueError("All notes must have matching embedding dimensions")
    return [list(note.embedding) for note in notes]


def _deterministic_coordinates(count: int) -> list[tuple[float, float]]:
    if count <= 0:
        return []
    if count == 1:
        return [(0.5, 0.5)]

    radius = 0.24
    return [
        (
            0.5 + radius * math.cos((2 * math.pi * index) / count),
            0.5 + radius * math.sin((2 * math.pi * index) / count),
        )
        for index in range(count)
    ]


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


def _static_cluster_description(notes: list[NoteRecord]) -> str:
    topics = [topic.replace("_", " ") for topic in _dominant_topics(notes)]
    if not topics:
        return f"{len(notes)} notes."
    return f"{len(notes)} notes about {', '.join(topics)}."


def _label_clusters_with_fallback(
    labeler: ClusterLabeler, clusters: dict[int, list[NoteRecord]]
) -> dict[int, str]:
    if not clusters:
        return {}
    fallback_labels = StaticClusterLabeler().label_clusters(clusters)
    try:
        labels = labeler.label_clusters(clusters)
    except Exception:
        LOGGER.warning("Cluster labeler failed; using static labels", exc_info=True)
        labels = {}
    return {
        cluster_id: (
            labels[cluster_id]
            if cluster_id in labels
            else fallback_labels.get(cluster_id, f"Cluster {cluster_id}")
        )
        for cluster_id in clusters
    }


def _describe_clusters_with_fallback(
    labeler: ClusterLabeler, clusters: dict[int, list[NoteRecord]]
) -> dict[int, str]:
    if not clusters:
        return {}
    fallback = StaticClusterLabeler().describe_clusters(clusters)
    describe = getattr(labeler, "describe_clusters", None)
    descriptions: dict[int, str] = {}
    if callable(describe):
        try:
            descriptions = describe(clusters)
        except Exception:
            LOGGER.warning(
                "Cluster describer failed; using static descriptions", exc_info=True
            )
    return {
        cluster_id: (
            descriptions[cluster_id]
            if cluster_id in descriptions
            else fallback.get(cluster_id, "")
        )
        for cluster_id in clusters
    }


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
        description=str(row["description"]),
    )
