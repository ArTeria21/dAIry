from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from dairy_bot.config import Settings
from dairy_bot.services import reviews
import dairy_bot.services as services
from dairy_bot.services.enrichment_db import EnrichmentStore


TZ = ZoneInfo("Europe/Vienna")


def _document(
    *,
    text: str = "A diary entry about finishing a difficult task.",
    content_hash: str = "hash-v1",
) -> reviews.CorpusDocument:
    return reviews.CorpusDocument(
        document_id="diary:2026-07-31T09:00",
        source_type="diary",
        path="2026/07/2026-07-31.md",
        heading="09:00",
        text=text,
        content_hash=content_hash,
        document_date=date(2026, 7, 31),
        first_seen=datetime(2026, 8, 1, tzinfo=TZ),
    )


def _semantic_types():
    return (
        getattr(services, "SemanticEmbeddingStore"),
        getattr(services, "SemanticEmbeddingService"),
        getattr(services, "DiarySemanticIndexer"),
    )


def test_AC_2_AC_5_shared_cache_calls_e5_once_and_uses_role_prefixes(tmp_path):
    store_type, service_type, _ = _semantic_types()
    calls: list[str] = []

    async def embed(text: str):
        calls.append(text)
        return [1.0, 0.5]

    store = store_type(tmp_path / "embeddings.sqlite3")
    embeddings = service_type(
        store=store,
        embed=embed,
        model="intfloat/multilingual-e5-large",
    )
    document = _document()

    first = asyncio.run(embeddings.embed_document(document))
    second = asyncio.run(embeddings.embed_document(document))
    query = asyncio.run(embeddings.embed_query("finishing versus avoidance"))

    assert first == second == (1.0, 0.5)
    assert query == (1.0, 0.5)
    assert calls == [
        f"passage: {document.text}",
        "query: finishing versus avoidance",
    ]
    stored = store.list_embeddings()
    assert [
        (
            item.document_id,
            item.content_hash,
            item.model,
            item.recipe_version,
            item.dimension,
            item.embedding,
        )
        for item in stored
    ] == [
        (
            document.document_id,
            document.content_hash,
            "intfloat/multilingual-e5-large",
            "e5-query-passage-v1",
            2,
            (1.0, 0.5),
        )
    ]


def test_AC_3_EC_1_EC_2_reviews_only_runtime_reindexes_changes_and_deletions(tmp_path):
    calls: list[str] = []

    class EmbeddingsResource:
        async def create(self, **kwargs):
            calls.append(kwargs["input"])
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[float(len(calls)), 0.25])]
            )

    class OpenAIClient:
        def __init__(self):
            self.embeddings = EmbeddingsResource()
            self.closed = False

        async def close(self):
            self.closed = True

    settings = Settings(
        _env_file=None,
        BOT_TOKEN="123:test",
        ALLOWED_USER_ID=42,
        OPENROUTER_API_KEY="sk-test",
        JOURNAL_DIR=tmp_path / "vault",
        ENRICHMENT_ENABLED=False,
        REVIEWS_ENABLED=True,
        WEB_PUBLIC_BASE_URL="https://diary.example.org",
        REVIEW_IMAGE_MODEL_NAME="test/primary-image",
        REVIEW_IMAGE_FALLBACK_MODEL_NAME="test/fallback-image",
        EMBEDDINGS_DB_PATH=tmp_path / "embeddings.sqlite3",
        EMBEDDING_MODEL_NAME="intfloat/multilingual-e5-large",
    )
    client = OpenAIClient()
    runtime = getattr(services, "build_semantic_runtime")(
        settings,
        openai_client=client,
    )
    assert runtime is not None

    document = _document()
    assert asyncio.run(runtime.indexer.sync([document])) is True
    assert asyncio.run(runtime.indexer.sync([document])) is True
    changed = replace(document, text="Changed diary entry.", content_hash="hash-v2")
    assert asyncio.run(runtime.indexer.sync([changed])) is True
    assert calls == [f"passage: {document.text}", "passage: Changed diary entry."]

    assert asyncio.run(runtime.indexer.sync([])) is True
    assert runtime.store.list_embeddings() == []
    state = runtime.store.get_state()
    assert (state.status, state.corpus_hash, state.next_retry_at) == (
        "ready",
        hashlib.sha256(b"").hexdigest(),
        None,
    )
    asyncio.run(runtime.close())
    assert client.closed is True


def test_legacy_embedding_column_survives_enrichment_startup_until_e5_cutover(
    tmp_path,
):
    enrichment_path = tmp_path / "enrichment.sqlite3"
    with sqlite3.connect(enrichment_path) as connection:
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
            INSERT INTO notes VALUES (
                '2026-07-31T09:00', '2026-07-31', '09:00',
                '2026/07/2026-07-31.md', 'Kept gist', 'calm', 0.7,
                '["reflection"]', 'Kept evidence', '[1.0, 0.5]'
            );
            """
        )

    enrichment = EnrichmentStore(enrichment_path)
    with sqlite3.connect(enrichment.db_path) as connection:
        connection.row_factory = sqlite3.Row
        note_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(notes)")
        }
        row = connection.execute(
            "SELECT gist, mood_evidence FROM notes WHERE id = ?",
            ("2026-07-31T09:00",),
        ).fetchone()
    assert "embedding" in note_columns
    assert tuple(row) == ("Kept gist", "Kept evidence")


def test_ERR_1_failed_reindex_retries_three_times_and_preserves_valid_vector(tmp_path):
    store_type, service_type, indexer_type = _semantic_types()
    store = store_type(tmp_path / "embeddings.sqlite3")
    calls: list[str] = []
    failures = False

    async def embed(text: str):
        nonlocal failures
        calls.append(text)
        if failures:
            raise RuntimeError("provider unavailable")
        return [1.0, 0.5]

    async def no_sleep(_: float):
        return None

    now = datetime(2026, 8, 5, 12, 0, tzinfo=TZ)
    embeddings = service_type(store=store, embed=embed, model="embed-v1")
    indexer = indexer_type(
        store=store,
        embeddings=embeddings,
        sleep=no_sleep,
        now=lambda: now,
    )
    original = _document()
    assert asyncio.run(indexer.sync([original])) is True

    failures = True
    changed = replace(original, text="Changed text.", content_hash="hash-v2")
    assert asyncio.run(indexer.sync([changed])) is False

    stored = store.list_embeddings()
    state = store.get_state()
    assert len(calls) == 4
    assert [(item.content_hash, item.embedding) for item in stored] == [
        ("hash-v1", (1.0, 0.5))
    ]
    assert state.status == "building"
    assert state.next_retry_at == now + timedelta(minutes=15)
