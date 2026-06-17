from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dairy_web.analysis import AnalysisCache, AnalysisService, OpenRouterClusterLabeler
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


class FakeStore:
    def __init__(self, notes: list[NoteRecord]):
        self.notes = notes

    def list_notes(self) -> list[NoteRecord]:
        return list(self.notes)


class FakeProjector:
    def __init__(self):
        self.calls = 0

    def project(self, vectors):
        self.calls += 1
        return [(float(index), float(index + 10)) for index, _ in enumerate(vectors)]


class FakeClusterer:
    def __init__(self, labels: list[int]):
        self.labels = labels
        self.calls = 0

    def cluster(self, vectors):
        self.calls += 1
        return list(self.labels)


class FakeLabeler:
    def __init__(self):
        self.calls = 0

    def label_clusters(self, clusters):
        self.calls += 1
        return {cluster_id: f"Cluster {cluster_id}" for cluster_id in clusters}


def fixed_now() -> datetime:
    return datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)


def test_AC_3_analysis_serves_cached_snapshot_without_recomputing(tmp_path):
    store = FakeStore(
        [
            note("2026-06-16T21:55", topics=["learning", "reflection"]),
            note("2026-06-17T10:15", topics=["learning"]),
        ]
    )
    projector = FakeProjector()
    clusterer = FakeClusterer([4, 4])
    labeler = FakeLabeler()
    service = AnalysisService(
        store=store,
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        clusterer=clusterer,
        labeler=labeler,
        now=fixed_now,
    )

    first = service.get_map()
    second = service.get_map()

    assert first.signature == second.signature
    assert [(point.id, point.x, point.y, point.cluster_id) for point in second.points] == [
        ("2026-06-16T21:55", 0.0, 10.0, 4),
        ("2026-06-17T10:15", 1.0, 11.0, 4),
    ]
    assert [(cluster.id, cluster.label, cluster.size, cluster.dominant_topics) for cluster in second.clusters] == [
        (4, "Cluster 4", 2, ["learning", "reflection"])
    ]
    assert projector.calls == 1
    assert clusterer.calls == 1
    assert labeler.calls == 1


def test_AC_4_force_rebuild_ignores_matching_cache_and_recomputes(tmp_path):
    store = FakeStore([note("2026-06-16T21:55")])
    projector = FakeProjector()
    clusterer = FakeClusterer([-1])
    labeler = FakeLabeler()
    service = AnalysisService(
        store=store,
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        clusterer=clusterer,
        labeler=labeler,
        now=fixed_now,
    )

    service.get_map()
    rebuilt = service.rebuild()

    assert rebuilt.n_points == 1
    assert rebuilt.n_clusters == 0
    assert projector.calls == 2
    assert clusterer.calls == 2
    assert labeler.calls == 0


def test_AC_4_changed_note_signature_recomputes_and_persists_new_points(tmp_path):
    store = FakeStore([note("2026-06-16T21:55")])
    projector = FakeProjector()
    clusterer = FakeClusterer([2])
    labeler = FakeLabeler()
    service = AnalysisService(
        store=store,
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        clusterer=clusterer,
        labeler=labeler,
        now=fixed_now,
    )

    first = service.get_map()
    store.notes.append(note("2026-06-17T10:15"))
    clusterer.labels = [2, 3]
    second = service.get_map()

    assert first.signature != second.signature
    assert [point.id for point in second.points] == [
        "2026-06-16T21:55",
        "2026-06-17T10:15",
    ]
    assert projector.calls == 2
    assert clusterer.calls == 2
    assert labeler.calls == 2


def test_EC_1_empty_notes_return_empty_snapshot_without_heavy_compute(tmp_path):
    projector = FakeProjector()
    clusterer = FakeClusterer([])
    labeler = FakeLabeler()
    service = AnalysisService(
        store=FakeStore([]),
        cache=AnalysisCache(tmp_path / "analysis_cache.sqlite3"),
        projector=projector,
        clusterer=clusterer,
        labeler=labeler,
        now=fixed_now,
    )

    snapshot = service.get_map()

    assert snapshot.points == []
    assert snapshot.clusters == []
    assert projector.calls == 0
    assert clusterer.calls == 0
    assert labeler.calls == 0


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
    assert calls == [
        (
            "openrouter/test-model",
            "Name this journal cluster in 2-4 words.\n"
            "Gists:\n"
            "- Gist for 2026-06-16T21:55\n"
            "- Gist for 2026-06-17T10:15",
        )
    ]
