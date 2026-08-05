from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dairy_web.data_access import EnrichmentReadStore


def _create_enrichment_db(path: Path) -> None:
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
                mood_evidence TEXT NOT NULL
            );
            CREATE TABLE note_entry_state (
                id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL
            );
            """
        )
        for index in (1, 2):
            note_id = f"2026-07-0{index}T09:00"
            connection.execute(
                "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    note_id,
                    f"2026-07-0{index}",
                    "09:00",
                    f"2026/07/2026-07-0{index}.md",
                    f"Gist {index}",
                    "calm",
                    0.8,
                    "[]",
                    f"Evidence {index}",
                ),
            )
            connection.execute(
                "INSERT INTO note_entry_state VALUES (?, ?)",
                (note_id, f"hash-{index}"),
            )


def _create_generation_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE semantic_generations (
                generation_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                corpus_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                recipe_version TEXT NOT NULL,
                dimension INTEGER NULL,
                next_retry_at TEXT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                published_at TEXT NULL
            );
            CREATE TABLE semantic_vectors (
                generation_id INTEGER NOT NULL,
                document_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                embedding TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (generation_id, document_id)
            );
            CREATE TABLE semantic_index_state (
                singleton INTEGER PRIMARY KEY,
                active_generation_id INTEGER NULL,
                previous_generation_id INTEGER NULL,
                building_generation_id INTEGER NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO semantic_generations VALUES (
                1, 'ready', 'raw-corpus', 'intfloat/multilingual-e5-large',
                'raw-v1', 2, NULL, '2026-08-05T08:00:00+00:00',
                '2026-08-05T08:00:00+00:00', '2026-08-05T08:00:00+00:00'
            );
            INSERT INTO semantic_generations VALUES (
                2, 'building', 'e5-corpus', 'intfloat/multilingual-e5-large',
                'e5-query-passage-v1', NULL, NULL,
                '2026-08-05T09:00:00+00:00',
                '2026-08-05T09:00:00+00:00', NULL
            );
            INSERT INTO semantic_index_state VALUES (
                1, 1, NULL, 2, '2026-08-05T09:00:00+00:00'
            );
            """
        )
        for index in (1, 2):
            document_id = f"diary:2026-07-0{index}T09:00"
            connection.execute(
                "INSERT INTO semantic_vectors VALUES (?, ?, ?, ?, ?, ?)",
                (
                    1,
                    document_id,
                    f"hash-{index}",
                    2,
                    json.dumps([float(index), 0.5]),
                    "2026-08-05T08:00:00+00:00",
                ),
            )
        connection.execute(
            "INSERT INTO semantic_vectors VALUES (?, ?, ?, ?, ?, ?)",
            (
                2,
                "diary:2026-07-01T09:00",
                "hash-1",
                2,
                json.dumps([11.0, 1.0]),
                "2026-08-05T09:00:00+00:00",
            ),
        )


def test_AC_2_AC_3_map_reads_one_complete_active_generation_during_and_after_cutover(
    tmp_path: Path,
):
    enrichment_path = tmp_path / "enrichment.sqlite3"
    embeddings_path = tmp_path / "embeddings.sqlite3"
    _create_enrichment_db(enrichment_path)
    _create_generation_db(embeddings_path)
    store = EnrichmentReadStore(enrichment_path, embeddings_path)

    assert store.semantic_signature() == (
        "raw-corpus|intfloat/multilingual-e5-large|raw-v1"
    )
    assert [note.embedding for note in store.list_notes()] == [
        [1.0, 0.5],
        [2.0, 0.5],
    ]

    with sqlite3.connect(embeddings_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO semantic_vectors VALUES (?, ?, ?, ?, ?, ?)",
            (
                2,
                "diary:2026-07-02T09:00",
                "hash-2",
                2,
                json.dumps([12.0, 1.0]),
                "2026-08-05T09:01:00+00:00",
            ),
        )
        connection.execute(
            """
            UPDATE semantic_generations
            SET status = 'ready', dimension = 2,
                published_at = '2026-08-05T09:01:00+00:00'
            WHERE generation_id = 2
            """
        )
        connection.execute(
            """
            UPDATE semantic_index_state
            SET active_generation_id = 2, previous_generation_id = 1,
                building_generation_id = NULL
            WHERE singleton = 1
            """
        )

    assert store.semantic_signature() == (
        "e5-corpus|intfloat/multilingual-e5-large|e5-query-passage-v1"
    )
    assert [note.embedding for note in store.list_notes()] == [
        [11.0, 1.0],
        [12.0, 1.0],
    ]
