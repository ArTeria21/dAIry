from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest

from dairy_web.analysis import (
    AnalysisCache,
    AnalysisService,
    HdbscanClusterer,
    OpenRouterClusterLabeler,
    UmapReducer,
    note_set_signature,
)
from dairy_web.data_access import NoteRecord


def note(
    note_id: str,
    *,
    embedding: list[float] | None = None,
    topics: list[str] | None = None,
    mood: str = "calm",
    confidence: float = 0.8,
) -> NoteRecord:
    return NoteRecord(
        id=note_id,
        date=note_id[:10],
        ts=note_id[11:16],
        note_path=f"{note_id[:4]}/{note_id[5:7]}/{note_id[:10]}.md",
        gist=f"Gist for {note_id}",
        mood=mood,
        mood_confidence=confidence,
        topics=topics or ["learning"],
        mood_evidence="The language is reflective.",
        embedding=embedding or [0.1, 0.2, 0.3],
    )


def note_id(index: int) -> str:
    return f"2026-06-{(index // 24) + 1:02d}T{index % 24:02d}:00"


def notes(count: int, *, dimensions: int = 3) -> list[NoteRecord]:
    return [
        note(
            note_id(index),
            embedding=[float(index + value) for value in range(dimensions)],
        )
        for index in range(count)
    ]


class FakeStore:
    def __init__(
        self,
        notes: list[NoteRecord],
        content_hashes: dict[str, str] | None = None,
    ):
        self.notes = notes
        self.content_hashes = content_hashes or {}

    def list_notes(self) -> list[NoteRecord]:
        return list(self.notes)

    def note_content_hashes(self) -> dict[str, str]:
        return dict(self.content_hashes)


class FakeProjector:
    def __init__(self):
        self.calls = 0

    def project(self, vectors):
        self.calls += 1
        return [(float(index), float(index + 10)) for index, _ in enumerate(vectors)]


class FailingProjector:
    def __init__(self):
        self.calls = 0

    def project(self, vectors):
        self.calls += 1
        raise ValueError("projection failed")


class FakeReducer:
    def __init__(self, dimensions: int = 10):
        self.dimensions = dimensions
        self.calls = 0
        self.input_dimensions: list[int] = []

    def reduce(self, vectors):
        self.calls += 1
        self.input_dimensions.extend(len(vector) for vector in vectors)
        return [
            [float(index + value) for value in range(self.dimensions)]
            for index, _ in enumerate(vectors)
        ]


class FakeClusterer:
    def __init__(self, labels: list[int]):
        self.labels = labels
        self.calls = 0
        self.input_dimensions: list[int] = []

    def cluster(self, vectors):
        self.calls += 1
        self.input_dimensions.extend(len(vector) for vector in vectors)
        return list(self.labels)


class FakeLabeler:
    def __init__(self):
        self.calls = 0

    def label_clusters(self, clusters):
        self.calls += 1
        return {cluster_id: f"Cluster {cluster_id}" for cluster_id in clusters}


class FailingLabeler:
    def label_clusters(self, clusters):
        raise RuntimeError("labeler unavailable")


def fixed_now() -> datetime:
    return datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)


def test_E9_note_set_signature_uses_content_hashes_when_available():
    notes = [
        note("2026-06-16T21:55"),
        note("2026-06-17T10:15"),
    ]
    old_style = note_set_signature(notes, {})
    with_hashes = note_set_signature(notes, {"2026-06-16T21:55": "hash-a"})
    changed_hash = note_set_signature(notes, {"2026-06-16T21:55": "hash-b"})
    expected_state = hashlib.sha256(
        "2026-06-16T21:55:hash-a|2026-06-17T10:15:".encode("utf-8")
    ).hexdigest()

    assert old_style.startswith("count=2;max=2026-06-17T10:15;ids=")
    assert with_hashes == f"count=2;max=2026-06-17T10:15;state={expected_state}"
    assert with_hashes != changed_hash


def test_AC_3_analysis_serves_cached_snapshot_without_recomputing(tmp_path):
    sample_notes = notes(15)
    sample_notes[0] = note(sample_notes[0].id, topics=["learning", "reflection"])
    store = FakeStore(sample_notes)
    projector = FakeProjector()
    reducer = FakeReducer()
    clusterer = FakeClusterer([4] * 15)
    labeler = FakeLabeler()
    service = AnalysisService(
        store=store,
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        reducer=reducer,
        clusterer=clusterer,
        labeler=labeler,
        now=fixed_now,
    )

    first = service.get_map()
    second = service.get_map()

    assert first.signature == second.signature
    assert [(point.id, point.x, point.y, point.cluster_id) for point in second.points[:2]] == [
        ("2026-06-01T00:00", 0.0, 10.0, 4),
        ("2026-06-01T01:00", 1.0, 11.0, 4),
    ]
    assert [(cluster.id, cluster.label, cluster.size, cluster.dominant_topics) for cluster in second.clusters] == [
        (4, "Cluster 4", 15, ["learning", "reflection"])
    ]
    assert second.n_noise == 0
    assert len(second.points) == second.n_noise + sum(
        cluster.size for cluster in second.clusters
    )
    assert projector.calls == 1
    assert reducer.calls == 1
    assert clusterer.calls == 1
    assert labeler.calls == 1


def test_S1_2_clusterer_receives_reduced_vectors_not_raw_embeddings(tmp_path):
    sample_notes = notes(15, dimensions=1024)
    projector = FakeProjector()
    reducer = FakeReducer(dimensions=10)
    clusterer = FakeClusterer([1] * 15)
    service = AnalysisService(
        store=FakeStore(sample_notes),
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        reducer=reducer,
        clusterer=clusterer,
        labeler=FakeLabeler(),
        now=fixed_now,
    )

    service.get_map()

    cached = service.get_map()

    assert cached.n_noise == 0
    assert set(reducer.input_dimensions) == {1024}
    assert set(clusterer.input_dimensions) == {10}


def test_AC_4_force_rebuild_ignores_matching_cache_and_recomputes(tmp_path):
    store = FakeStore(notes(15))
    projector = FakeProjector()
    reducer = FakeReducer()
    clusterer = FakeClusterer([-1] * 15)
    labeler = FakeLabeler()
    service = AnalysisService(
        store=store,
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        reducer=reducer,
        clusterer=clusterer,
        labeler=labeler,
        now=fixed_now,
    )

    service.get_map()
    rebuilt = service.rebuild()

    assert rebuilt.n_points == 15
    assert rebuilt.n_clusters == 0
    assert rebuilt.n_noise == 15
    assert projector.calls == 2
    assert reducer.calls == 2
    assert clusterer.calls == 2
    assert labeler.calls == 0


def test_E4_all_noise_keeps_points_without_cluster_summaries(tmp_path):
    service = AnalysisService(
        store=FakeStore(notes(15)),
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=FakeProjector(),
        reducer=FakeReducer(),
        clusterer=FakeClusterer([-1] * 15),
        labeler=FakeLabeler(),
        now=fixed_now,
    )

    snapshot = service.get_map()

    assert len(snapshot.points) == 15
    assert {point.cluster_id for point in snapshot.points} == {-1}
    assert snapshot.clusters == []
    assert snapshot.n_noise == 15


def test_AC_3_labeler_failure_uses_static_labels_without_breaking_snapshot(tmp_path):
    service = AnalysisService(
        store=FakeStore(notes(15)),
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=FakeProjector(),
        reducer=FakeReducer(),
        clusterer=FakeClusterer([7] * 15),
        labeler=FailingLabeler(),
        now=fixed_now,
    )

    snapshot = service.get_map()

    assert [(cluster.id, cluster.label, cluster.size) for cluster in snapshot.clusters] == [
        (7, "learning", 15)
    ]
    assert len(snapshot.points) == snapshot.n_noise + sum(
        cluster.size for cluster in snapshot.clusters
    )


def test_AC_4_changed_content_hash_recomputes_same_note_set(tmp_path):
    sample_notes = notes(15)
    store = FakeStore(
        sample_notes,
        {item.id: f"hash-{index}" for index, item in enumerate(sample_notes)},
    )
    projector = FakeProjector()
    reducer = FakeReducer()
    clusterer = FakeClusterer([2] * 15)
    labeler = FakeLabeler()
    service = AnalysisService(
        store=store,
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        reducer=reducer,
        clusterer=clusterer,
        labeler=labeler,
        now=fixed_now,
    )

    first = service.get_map()
    store.content_hashes[sample_notes[0].id] = "hash-changed"
    clusterer.labels = [3] * 15
    second = service.get_map()

    assert first.signature != second.signature
    assert [point.id for point in second.points] == [item.id for item in sample_notes]
    assert {point.cluster_id for point in second.points} == {3}
    assert projector.calls == 2
    assert reducer.calls == 2
    assert clusterer.calls == 2
    assert labeler.calls == 2


def test_EC_1_empty_notes_return_empty_snapshot_without_heavy_compute(tmp_path):
    projector = FakeProjector()
    reducer = FakeReducer()
    clusterer = FakeClusterer([])
    labeler = FakeLabeler()
    service = AnalysisService(
        store=FakeStore([]),
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        reducer=reducer,
        clusterer=clusterer,
        labeler=labeler,
        now=fixed_now,
    )

    snapshot = service.get_map()

    assert snapshot.points == []
    assert snapshot.clusters == []
    assert snapshot.n_noise == 0
    assert projector.calls == 0
    assert reducer.calls == 0
    assert clusterer.calls == 0
    assert labeler.calls == 0


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_small_note_sets_use_deterministic_projection_without_umap(tmp_path, count):
    projector = FakeProjector()
    reducer = FakeReducer()
    clusterer = FakeClusterer([])
    service = AnalysisService(
        store=FakeStore(notes(count)),
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        reducer=reducer,
        clusterer=clusterer,
        labeler=FakeLabeler(),
        now=fixed_now,
    )

    snapshot = service.get_map()

    assert len(snapshot.points) == count
    assert all(0 <= point.x <= 1 and 0 <= point.y <= 1 for point in snapshot.points)
    assert {point.cluster_id for point in snapshot.points} == {-1}
    assert snapshot.n_noise == count
    assert projector.calls == 0
    assert reducer.calls == 0
    assert clusterer.calls == 0


def test_projection_failure_falls_back_to_deterministic_coordinates(tmp_path):
    projector = FailingProjector()
    service = AnalysisService(
        store=FakeStore(notes(5)),
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        reducer=FakeReducer(),
        clusterer=FakeClusterer([]),
        labeler=FakeLabeler(),
        now=fixed_now,
    )

    snapshot = service.get_map()

    assert len(snapshot.points) == 5
    assert all(0 <= point.x <= 1 and 0 <= point.y <= 1 for point in snapshot.points)
    assert snapshot.n_noise == 5
    assert projector.calls == 1


def test_E10_old_cache_schema_without_n_noise_is_recreated(tmp_path):
    cache_path = tmp_path / "analysis_cache.sqlite3"
    with sqlite3.connect(cache_path) as conn:
        conn.executescript(
            """
            CREATE TABLE metadata (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                signature TEXT NOT NULL,
                computed_at TEXT NOT NULL
            );
            CREATE TABLE points (
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
            CREATE TABLE clusters (
                cluster_id INTEGER PRIMARY KEY,
                label TEXT NOT NULL,
                size INTEGER NOT NULL,
                dominant_topics_json TEXT NOT NULL
            );
            INSERT INTO metadata (id, signature, computed_at)
            VALUES (1, 'stale', '2026-06-01T00:00:00+00:00');
            """
        )

    service = AnalysisService(
        store=FakeStore([]),
        cache=AnalysisCache(cache_path),
        projector=FakeProjector(),
        reducer=FakeReducer(),
        clusterer=FakeClusterer([]),
        labeler=FakeLabeler(),
        now=fixed_now,
    )

    snapshot = service.get_map()

    assert snapshot.n_noise == 0
    with sqlite3.connect(cache_path) as conn:
        assert conn.execute("SELECT n_noise FROM metadata").fetchone() == (0,)


def test_E2_single_note_uses_center_point_without_projection_or_clustering(tmp_path):
    projector = FakeProjector()
    reducer = FakeReducer()
    clusterer = FakeClusterer([])
    service = AnalysisService(
        store=FakeStore([note("2026-06-16T21:55")]),
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        reducer=reducer,
        clusterer=clusterer,
        labeler=FakeLabeler(),
        now=fixed_now,
    )

    snapshot = service.get_map()

    assert [(point.x, point.y, point.cluster_id) for point in snapshot.points] == [
        (0.5, 0.5, -1)
    ]
    assert snapshot.clusters == []
    assert snapshot.n_noise == 1
    assert projector.calls == 0
    assert reducer.calls == 0
    assert clusterer.calls == 0


def test_E3_AC_6_notes_below_cluster_threshold_are_projected_but_not_clustered(tmp_path):
    projector = FakeProjector()
    reducer = FakeReducer()
    clusterer = FakeClusterer([1] * 14)
    service = AnalysisService(
        store=FakeStore(notes(14)),
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        reducer=reducer,
        clusterer=clusterer,
        labeler=FakeLabeler(),
        now=fixed_now,
    )

    snapshot = service.get_map()

    assert len(snapshot.points) == 14
    assert {point.cluster_id for point in snapshot.points} == {-1}
    assert snapshot.clusters == []
    assert snapshot.n_noise == 14
    assert projector.calls == 1
    assert reducer.calls == 0
    assert clusterer.calls == 0


def test_EC_2_analysis_rejects_mixed_embedding_dimensions(tmp_path):
    service = AnalysisService(
        store=FakeStore(
            [
                note("2026-06-16T21:55", embedding=[0.1, 0.2, 0.3]),
                note("2026-06-17T10:15", embedding=[0.1, 0.2]),
            ]
        ),
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=FakeProjector(),
        reducer=FakeReducer(),
        clusterer=FakeClusterer([1, 1]),
        labeler=FakeLabeler(),
        now=fixed_now,
    )

    with pytest.raises(ValueError, match="embedding dimensions"):
        service.get_map()


def test_AC_4_openrouter_cluster_labeler_uses_member_gists_for_short_labels():
    calls: list[tuple[str, str]] = []

    def complete(model: str, prompt: str) -> str:
        calls.append((model, prompt))
        return "Language Practice"

    labeler = OpenRouterClusterLabeler(
        model_name="openrouter/test-model",
        complete=complete,
        max_gists=2,
    )

    labels = labeler.label_clusters(
        {
            8: [
                note("2026-06-16T21:55"),
                note("2026-06-17T10:15"),
                note("2026-06-18T09:00"),
            ]
        }
    )

    assert labels == {8: "Language Practice"}
    assert labeler.timeout == 20.0
    assert calls == [
        (
            "openrouter/test-model",
            "Name this journal cluster in 2-4 words.\n"
            "Gists:\n"
            "- Gist for 2026-06-16T21:55\n"
            "- Gist for 2026-06-17T10:15",
        )
    ]


def test_E5_openrouter_cluster_labeler_falls_back_and_logs_warning(caplog):
    caplog.set_level("WARNING", logger="dairy_web.analysis")

    def complete(model: str, prompt: str) -> str:
        raise TimeoutError("OpenRouter timed out")

    labeler = OpenRouterClusterLabeler(
        model_name="openrouter/test-model",
        complete=complete,
    )

    labels = labeler.label_clusters(
        {
            8: [
                note("2026-06-16T21:55", topics=["learning", "reflection"]),
                note("2026-06-17T10:15", topics=["learning"]),
            ]
        }
    )

    assert labels == {8: "learning / reflection"}
    assert "OpenRouter cluster label failed" in caplog.text


def test_E6_openrouter_cluster_labeler_uses_cluster_id_for_empty_labels():
    labeler = OpenRouterClusterLabeler(
        model_name="openrouter/test-model",
        complete=lambda model, prompt: "   \n . ",
    )

    labels = labeler.label_clusters({8: [note("2026-06-16T21:55")]})

    assert labels == {8: "Cluster 8"}


@pytest.mark.slow
def test_AC_1_real_reducer_and_hdbscan_find_clusters_on_cosine_synthetic_data():
    import numpy as np

    rng = np.random.default_rng(42)
    vectors: list[list[float]] = []
    for group in range(3):
        center = np.zeros(1024)
        center[group] = 1.0
        for _ in range(30):
            vector = center + rng.normal(0, 0.02, size=1024)
            vector = vector / np.linalg.norm(vector)
            vectors.append(vector.tolist())

    reduced = UmapReducer().reduce(vectors)
    labels = HdbscanClusterer().cluster(reduced)

    cluster_ids = {label for label in labels if label != -1}
    n_noise = sum(1 for label in labels if label == -1)
    assert len(cluster_ids) >= 2
    assert n_noise / len(labels) < 0.5
