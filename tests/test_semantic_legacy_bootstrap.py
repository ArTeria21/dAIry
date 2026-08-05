from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from dairy_bot.services import (
    DiarySemanticIndexer,
    SemanticEmbeddingService,
    SemanticEmbeddingStore,
)
from dairy_bot.services.diary_corpus import CorpusDocument
from dairy_bot.services.semantic_embeddings import E5_MODEL, RAW_RECIPE_VERSION


TZ = ZoneInfo("Europe/Vienna")


def test_AC_1_indexer_activates_legacy_raw_snapshot_before_first_e5_provider_call(
    tmp_path: Path,
):
    text = "A legacy diary entry."
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    document = CorpusDocument(
        document_id="diary:2026-07-31T09:00",
        source_type="diary",
        path="2026/07/2026-07-31.md",
        heading="09:00",
        text=text,
        content_hash=content_hash,
        document_date=date(2026, 7, 31),
        first_seen=datetime(2026, 8, 1, tzinfo=TZ),
    )
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
                embedding TEXT NULL
            );
            CREATE TABLE note_entry_state (
                id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-07-31T09:00",
                "2026-07-31",
                "09:00",
                document.path,
                "Legacy gist",
                "calm",
                0.8,
                "[]",
                "Legacy evidence",
                json.dumps([1.0, 0.5]),
            ),
        )
        connection.execute(
            "INSERT INTO note_entry_state VALUES (?, ?)",
            ("2026-07-31T09:00", content_hash),
        )

    store = SemanticEmbeddingStore(tmp_path / "embeddings.sqlite3")
    recipes_seen_by_provider: list[str | None] = []

    async def unavailable_e5(text: str) -> Sequence[float]:
        active = store.get_active_generation()
        recipes_seen_by_provider.append(
            None if active is None else active.recipe_version
        )
        raise RuntimeError("E5 provider unavailable")

    async def no_sleep(_: float) -> None:
        return None

    service = SemanticEmbeddingService(
        store=store,
        embed=unavailable_e5,
        model=E5_MODEL,
    )
    indexer = DiarySemanticIndexer(
        store=store,
        embeddings=service,
        sleep=no_sleep,
        legacy_enrichment_db_path=enrichment_path,
    )

    assert asyncio.run(indexer.sync([document])) is False

    active = store.get_active_generation()
    assert recipes_seen_by_provider == [RAW_RECIPE_VERSION] * 3
    assert active is not None and active.recipe_version == RAW_RECIPE_VERSION
    assert [item.embedding for item in store.list_embeddings()] == [(1.0, 0.5)]
