from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

import pytest

from dairy_bot.services import (
    DiarySemanticIndexer,
    SemanticEmbeddingService,
    SemanticEmbeddingStore,
)
from dairy_bot.services.diary_corpus import CorpusDocument
from dairy_bot.services.enrichment_db import EnrichmentStore
from dairy_bot.services.semantic_embeddings import (
    E5_MODEL,
    E5_RECIPE_VERSION,
    RAW_RECIPE_VERSION,
    corpus_hash,
)


TZ = ZoneInfo("Europe/Vienna")


def _documents(count: int = 2) -> list[CorpusDocument]:
    documents: list[CorpusDocument] = []
    for index in range(count):
        document_date = date(2026, 6, 1) + timedelta(days=index)
        documents.append(
            CorpusDocument(
            document_id=f"diary:{document_date.isoformat()}T09:00",
            source_type="diary",
            path=(
                f"{document_date.year}/{document_date.month:02d}/"
                f"{document_date.isoformat()}.md"
            ),
            heading="09:00",
            text=f"Diary passage {index + 1}.",
            content_hash=hashlib.sha256(
                f"Diary passage {index + 1}.".encode("utf-8")
            ).hexdigest(),
            document_date=document_date,
            first_seen=datetime(2026, 8, 1, tzinfo=TZ),
            )
        )
    return documents


def _create_legacy_enrichment_db(
    path: Path,
    documents: Sequence[CorpusDocument],
) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE notes (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                ts TEXT NOT NULL,
                note_path TEXT NOT NULL,
                gist TEXT NOT NULL,
                mood TEXT NOT NULL,
                mood_confidence REAL NOT NULL,
                topics_json TEXT NOT NULL,
                mood_evidence TEXT NOT NULL,
                embedding TEXT NOT NULL
            );
            CREATE TABLE note_entry_state (
                id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL
            );
            """
        )
        for index, document in enumerate(documents, start=1):
            note_id = document.document_id.removeprefix("diary:")
            connection.execute(
                """
                INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note_id,
                    document.document_date.isoformat(),
                    "09:00",
                    document.path,
                    f"Gist {index}",
                    "calm",
                    0.8,
                    "[]",
                    f"Evidence {index}",
                    json.dumps([float(index), 0.5]),
                ),
            )
            connection.execute(
                "INSERT INTO note_entry_state VALUES (?, ?)",
                (note_id, document.content_hash),
            )


def _column_names(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }


def test_AC_1_imports_compatible_legacy_vectors_as_active_raw_snapshot_without_provider_calls(
    tmp_path: Path,
):
    documents = _documents()
    enrichment_path = tmp_path / "enrichment.sqlite3"
    _create_legacy_enrichment_db(enrichment_path, documents)
    store = SemanticEmbeddingStore(tmp_path / "embeddings.sqlite3")
    provider_calls: list[str] = []

    async def embed(text: str) -> Sequence[float]:
        provider_calls.append(text)
        return [99.0, 99.0]

    SemanticEmbeddingService(store=store, embed=embed, model=E5_MODEL)
    store.import_legacy_embeddings(
        enrichment_path,
        documents,
        model=E5_MODEL,
    )

    active = store.get_active_generation()
    assert active is not None
    assert (
        active.status,
        active.corpus_hash,
        active.model,
        active.recipe_version,
        active.dimension,
    ) == (
        "ready",
        corpus_hash(documents),
        E5_MODEL,
        RAW_RECIPE_VERSION,
        2,
    )
    assert [item.embedding for item in store.list_embeddings()] == [
        (1.0, 0.5),
        (2.0, 0.5),
    ]
    assert provider_calls == []
    assert "embedding" in _column_names(enrichment_path, "notes")


def test_AC_2_AC_3_reader_observes_complete_raw_or_e5_snapshot_during_rebuild(
    tmp_path: Path,
):
    documents = _documents()
    enrichment_path = tmp_path / "enrichment.sqlite3"
    _create_legacy_enrichment_db(enrichment_path, documents)
    store = SemanticEmbeddingStore(tmp_path / "embeddings.sqlite3")
    store.import_legacy_embeddings(enrichment_path, documents, model=E5_MODEL)
    raw_generation = store.get_active_generation()
    assert raw_generation is not None

    async def scenario():
        second_started = asyncio.Event()
        release_second = asyncio.Event()

        async def embed(text: str) -> Sequence[float]:
            if text == f"passage: {documents[1].text}":
                second_started.set()
                await release_second.wait()
            index = 1 if text.endswith("1.") else 2
            return [10.0 + index, 1.0]

        service = SemanticEmbeddingService(store=store, embed=embed, model=E5_MODEL)
        indexer = DiarySemanticIndexer(store=store, embeddings=service)
        task = asyncio.create_task(indexer.sync(documents))
        await second_started.wait()

        during = store.list_embeddings()
        active_during = store.get_active_generation()
        building_during = store.get_building_generation()
        assert active_during is not None
        assert active_during.generation_id == raw_generation.generation_id
        assert [(item.document_id, item.embedding) for item in during] == [
            (documents[0].document_id, (1.0, 0.5)),
            (documents[1].document_id, (2.0, 0.5)),
        ]
        assert building_during is not None
        assert [
            item.document_id
            for item in store.list_embeddings(
                generation_id=building_during.generation_id
            )
        ] == [documents[0].document_id]

        release_second.set()
        assert await task is True

    asyncio.run(scenario())

    active_after = store.get_active_generation()
    assert active_after is not None
    assert active_after.generation_id != raw_generation.generation_id
    assert [(item.document_id, item.embedding) for item in store.list_embeddings()] == [
        (documents[0].document_id, (11.0, 1.0)),
        (documents[1].document_id, (12.0, 1.0)),
    ]


def test_AC_4_unchanged_sync_is_write_free_and_batches_at_most_32_inputs(
    tmp_path: Path,
):
    documents = _documents(33)
    store = SemanticEmbeddingStore(tmp_path / "embeddings.sqlite3")
    batches: list[tuple[str, ...]] = []

    async def embed_many(inputs: Sequence[str]) -> Sequence[Sequence[float]]:
        batches.append(tuple(inputs))
        return [[float(index), 1.0] for index, _ in enumerate(inputs, start=1)]

    service = SemanticEmbeddingService(
        store=store,
        embed_many=embed_many,
        model=E5_MODEL,
    )
    indexer = DiarySemanticIndexer(store=store, embeddings=service)
    assert asyncio.run(indexer.sync(documents)) is True
    assert [len(batch) for batch in batches] == [32, 1]
    assert all(text.startswith("passage: ") for batch in batches for text in batch)
    batches_before_noop = list(batches)

    active_before = store.get_active_generation()
    assert active_before is not None
    observer = sqlite3.connect(store.db_path)
    data_version_before = int(observer.execute("PRAGMA data_version").fetchone()[0])

    assert asyncio.run(indexer.sync(documents)) is True

    data_version_after = int(observer.execute("PRAGMA data_version").fetchone()[0])
    observer.close()
    assert batches == batches_before_noop
    assert store.get_active_generation() == active_before
    assert store.get_building_generation() is None
    assert data_version_after == data_version_before


@pytest.mark.parametrize(
    "case",
    ["empty", "nan", "infinity", "mixed-dimension"],
)
def test_AC_5_invalid_vectors_never_replace_the_active_generation(
    tmp_path: Path,
    case: str,
):
    documents = _documents()
    enrichment_path = tmp_path / "enrichment.sqlite3"
    _create_legacy_enrichment_db(enrichment_path, documents)
    store = SemanticEmbeddingStore(tmp_path / "embeddings.sqlite3")
    store.import_legacy_embeddings(enrichment_path, documents, model=E5_MODEL)
    raw_generation = store.get_active_generation()
    assert raw_generation is not None

    async def embed_many(inputs: Sequence[str]) -> Sequence[Sequence[float]]:
        if case == "empty":
            return [[] for _ in inputs]
        if case == "nan":
            return [[float("nan"), 1.0] for _ in inputs]
        if case == "infinity":
            return [[float("inf"), 1.0] for _ in inputs]
        return [[1.0, 1.0], [1.0]]

    async def no_sleep(_: float) -> None:
        return None

    service = SemanticEmbeddingService(
        store=store,
        embed_many=embed_many,
        model=E5_MODEL,
    )
    indexer = DiarySemanticIndexer(
        store=store,
        embeddings=service,
        sleep=no_sleep,
    )

    assert asyncio.run(indexer.sync(documents)) is False
    active_after = store.get_active_generation()
    building = store.get_building_generation()
    assert active_after is not None
    assert active_after.generation_id == raw_generation.generation_id
    assert active_after.recipe_version == RAW_RECIPE_VERSION
    assert [item.embedding for item in store.list_embeddings()] == [
        (1.0, 0.5),
        (2.0, 0.5),
    ]
    assert building is not None
    assert store.list_embeddings(generation_id=building.generation_id) == []


def test_AC_6_e5_publish_keeps_previous_snapshot_then_removes_legacy_column(
    tmp_path: Path,
):
    documents = _documents()
    enrichment_path = tmp_path / "enrichment.sqlite3"
    _create_legacy_enrichment_db(enrichment_path, documents)

    EnrichmentStore(enrichment_path)
    assert "embedding" in _column_names(enrichment_path, "notes")

    store = SemanticEmbeddingStore(tmp_path / "embeddings.sqlite3")
    store.import_legacy_embeddings(enrichment_path, documents, model=E5_MODEL)
    raw_generation = store.get_active_generation()
    assert raw_generation is not None

    async def embed(text: str) -> Sequence[float]:
        index = 1 if text.endswith("1.") else 2
        return [20.0 + index, 2.0]

    service = SemanticEmbeddingService(store=store, embed=embed, model=E5_MODEL)
    indexer = DiarySemanticIndexer(
        store=store,
        embeddings=service,
        legacy_enrichment_db_path=enrichment_path,
    )
    assert asyncio.run(indexer.sync(documents)) is True

    active = store.get_active_generation()
    previous = store.get_previous_generation()
    assert active is not None and previous is not None
    assert (active.recipe_version, previous.recipe_version) == (
        E5_RECIPE_VERSION,
        RAW_RECIPE_VERSION,
    )
    assert previous.generation_id == raw_generation.generation_id
    assert [
        item.embedding
        for item in store.list_embeddings(generation_id=previous.generation_id)
    ] == [(1.0, 0.5), (2.0, 0.5)]
    assert "embedding" not in _column_names(enrichment_path, "notes")
    with sqlite3.connect(enrichment_path) as connection:
        rows = connection.execute(
            "SELECT gist, mood_evidence FROM notes ORDER BY id"
        ).fetchall()
    assert rows == [("Gist 1", "Evidence 1"), ("Gist 2", "Evidence 2")]


def test_EC_1_EC_2_restart_resumes_partial_generation_and_publishes_latest_hash(
    tmp_path: Path,
):
    documents = _documents()
    enrichment_path = tmp_path / "enrichment.sqlite3"
    _create_legacy_enrichment_db(enrichment_path, documents)
    db_path = tmp_path / "embeddings.sqlite3"
    store = SemanticEmbeddingStore(db_path)
    store.import_legacy_embeddings(enrichment_path, documents, model=E5_MODEL)

    class SimulatedCrash(BaseException):
        pass

    first_process_calls: list[str] = []

    async def crashing_embed(text: str) -> Sequence[float]:
        first_process_calls.append(text)
        if text == f"passage: {documents[1].text}":
            raise SimulatedCrash
        return [31.0, 3.0]

    first_service = SemanticEmbeddingService(
        store=store,
        embed=crashing_embed,
        model=E5_MODEL,
    )
    first_indexer = DiarySemanticIndexer(store=store, embeddings=first_service)
    with pytest.raises(SimulatedCrash):
        asyncio.run(first_indexer.sync(documents))

    building_before_restart = store.get_building_generation()
    assert building_before_restart is not None
    assert [
        item.document_id
        for item in store.list_embeddings(
            generation_id=building_before_restart.generation_id
        )
    ] == [documents[0].document_id]

    changed = replace(
        documents[1],
        text="Diary passage 2 changed while rebuilding.",
        content_hash=hashlib.sha256(
            b"Diary passage 2 changed while rebuilding."
        ).hexdigest(),
    )
    second_process_calls: list[str] = []

    async def resumed_embed(text: str) -> Sequence[float]:
        second_process_calls.append(text)
        return [32.0, 3.0]

    restarted_store = SemanticEmbeddingStore(db_path)
    restarted_service = SemanticEmbeddingService(
        store=restarted_store,
        embed=resumed_embed,
        model=E5_MODEL,
    )
    restarted_indexer = DiarySemanticIndexer(
        store=restarted_store,
        embeddings=restarted_service,
    )
    latest_documents = [documents[0], changed]
    assert asyncio.run(restarted_indexer.sync(latest_documents)) is True

    active = restarted_store.get_active_generation()
    assert active is not None
    assert active.generation_id == building_before_restart.generation_id
    assert active.corpus_hash == corpus_hash(latest_documents)
    assert second_process_calls == [f"passage: {changed.text}"]
    assert [
        (item.document_id, item.content_hash, item.embedding)
        for item in restarted_store.list_embeddings()
    ] == [
        (documents[0].document_id, documents[0].content_hash, (31.0, 3.0)),
        (changed.document_id, changed.content_hash, (32.0, 3.0)),
    ]


def test_ERR_1_three_provider_failures_schedule_retry_without_hiding_active_snapshot(
    tmp_path: Path,
):
    documents = _documents()
    enrichment_path = tmp_path / "enrichment.sqlite3"
    _create_legacy_enrichment_db(enrichment_path, documents)
    store = SemanticEmbeddingStore(tmp_path / "embeddings.sqlite3")
    store.import_legacy_embeddings(enrichment_path, documents, model=E5_MODEL)
    raw_generation = store.get_active_generation()
    assert raw_generation is not None
    calls: list[str] = []

    async def unavailable(text: str) -> Sequence[float]:
        calls.append(text)
        raise RuntimeError("provider unavailable")

    async def no_sleep(_: float) -> None:
        return None

    now = datetime(2026, 8, 5, 12, 0, tzinfo=TZ)
    service = SemanticEmbeddingService(store=store, embed=unavailable, model=E5_MODEL)
    indexer = DiarySemanticIndexer(
        store=store,
        embeddings=service,
        sleep=no_sleep,
        now=lambda: now,
    )

    assert asyncio.run(indexer.sync(documents)) is False

    active = store.get_active_generation()
    building = store.get_building_generation()
    assert len(calls) == 3
    assert active is not None and active.generation_id == raw_generation.generation_id
    assert [item.embedding for item in store.list_embeddings()] == [
        (1.0, 0.5),
        (2.0, 0.5),
    ]
    assert building is not None
    assert building.status == "building"
    assert building.next_retry_at == now + timedelta(minutes=15)
