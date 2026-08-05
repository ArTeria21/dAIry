from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dairy_bot.services.enrichment_db import (
    EnrichmentStore,
    drop_legacy_embedding_column,
)
from dairy_bot.services.reviews import ReviewStore
from dairy_bot.services.semantic_embeddings import SemanticEmbeddingStore


class SimulatedMigrationCrash(RuntimeError):
    pass


SEMANTIC_V1_STEPS = (
    "rename_legacy_vectors",
    "rename_legacy_state",
    "create_generations",
    "create_vectors",
    "create_index_state",
    "seed_index_state",
    "copy_generation",
    "copy_vectors",
    "copy_index_state",
    "drop_legacy_vectors",
    "drop_legacy_state",
    "set_user_version",
)

ENRICHMENT_V0_STEPS = (
    "create_notes",
    "create_days",
    "create_note_entry_state",
    "create_file_state",
    "create_nullable_notes",
    "copy_nullable_notes",
    "drop_legacy_notes",
    "rename_nullable_notes",
    "set_user_version",
)

ENRICHMENT_DROP_STEPS = (
    "drop_stale_without_embedding",
    "create_without_embedding",
    "copy_without_embedding",
    "drop_notes_with_embedding",
    "rename_without_embedding",
    "set_user_version",
)

REVIEW_V2_STEPS = (
    "create_reviews",
    "create_review_sources",
    "create_review_sources_index",
    "create_corpus_documents",
    "create_generation_jobs",
    "create_telegram_deliveries",
    "add_retrieval_model",
    "add_retrieval_recipe",
    "add_attempt_count",
    "add_next_attempt_at",
    "add_last_error",
    "drop_corpus_embeddings",
    "create_review_metadata",
    "create_review_audit",
    "set_user_version",
)


def _crash_at(target: str):
    def hook(step: str) -> None:
        if step == target:
            raise SimulatedMigrationCrash(step)

    return hook


def _version(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _columns(path: Path, table: str) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[1]): int(row[3])
            for row in connection.execute(f"PRAGMA table_info({table})")
        }


def _fingerprint(path: Path) -> tuple[object, ...]:
    with sqlite3.connect(path) as connection:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        schema = tuple(
            connection.execute(
                """
                SELECT type, name, sql FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
        )
        table_names = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        rows = tuple(
            (
                table,
                tuple(connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')),
            )
            for table in table_names
        )
    return version, schema, rows


def _create_semantic_v1(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE semantic_embeddings (
                document_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                recipe_version TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                embedding TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE semantic_index_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                status TEXT NOT NULL,
                corpus_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                recipe_version TEXT NOT NULL,
                next_retry_at TEXT NULL,
                updated_at TEXT NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
        connection.executemany(
            """
            INSERT INTO semantic_embeddings VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "diary:2026-07-30T09:00",
                    "hash-1",
                    "legacy/model",
                    "raw-v1",
                    2,
                    json.dumps([1.0, 0.5]),
                    "2026-08-01T00:00:00+00:00",
                ),
                (
                    "diary:2026-07-31T09:00",
                    "hash-2",
                    "legacy/model",
                    "raw-v1",
                    2,
                    json.dumps([2.0, 0.5]),
                    "2026-08-01T00:00:00+00:00",
                ),
            ],
        )
        connection.execute(
            """
            INSERT INTO semantic_index_state VALUES (1, ?, ?, ?, ?, NULL, ?)
            """,
            (
                "ready",
                "corpus-v1",
                "legacy/model",
                "raw-v1",
                "2026-08-01T00:00:00+00:00",
            ),
        )


@pytest.mark.parametrize("fault_step", SEMANTIC_V1_STEPS)
def test_AC_5_4_semantic_migration_resumes_after_every_fault(
    tmp_path: Path,
    fault_step: str,
):
    path = tmp_path / fault_step / "embeddings.sqlite3"
    path.parent.mkdir()
    _create_semantic_v1(path)

    with pytest.raises(SimulatedMigrationCrash, match=fault_step):
        SemanticEmbeddingStore(path, migration_hook=_crash_at(fault_step))

    store = SemanticEmbeddingStore(path)
    active = store.get_active_generation()
    assert active is not None and active.corpus_hash == "corpus-v1"
    assert [item.embedding for item in store.list_embeddings()] == [
        (1.0, 0.5),
        (2.0, 0.5),
    ]
    assert _version(path) == 2


def _create_legacy_enrichment(path: Path) -> None:
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
            PRAGMA user_version = 0;
            """
        )
        connection.execute(
            """
            INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-31T09:00",
                "2026-07-31",
                "09:00",
                "2026/07/2026-07-31.md",
                "Preserved gist",
                "calm",
                0.8,
                '["reflection"]',
                "Preserved evidence",
                "[1.0, 0.5]",
            ),
        )


@pytest.mark.parametrize("fault_step", ENRICHMENT_V0_STEPS)
def test_AC_5_4_enrichment_migration_resumes_after_every_fault(
    tmp_path: Path,
    fault_step: str,
):
    path = tmp_path / fault_step / "enrichment.sqlite3"
    path.parent.mkdir()
    _create_legacy_enrichment(path)

    with pytest.raises(SimulatedMigrationCrash, match=fault_step):
        EnrichmentStore(path, migration_hook=_crash_at(fault_step))

    store = EnrichmentStore(path)
    assert store.get_note("2026-07-31T09:00")["gist"] == "Preserved gist"
    assert _columns(path, "notes")["embedding"] == 0
    assert _version(path) == 1


@pytest.mark.parametrize("fault_step", ENRICHMENT_DROP_STEPS)
def test_AC_5_4_legacy_column_drop_resumes_after_every_fault(
    tmp_path: Path,
    fault_step: str,
):
    path = tmp_path / fault_step / "enrichment.sqlite3"
    path.parent.mkdir()
    _create_legacy_enrichment(path)
    EnrichmentStore(path)

    with pytest.raises(SimulatedMigrationCrash, match=fault_step):
        drop_legacy_embedding_column(
            path,
            migration_hook=_crash_at(fault_step),
        )

    assert drop_legacy_embedding_column(path) is True
    assert "embedding" not in _columns(path, "notes")
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT gist, mood_evidence FROM notes"
        ).fetchone()
    assert row == ("Preserved gist", "Preserved evidence")
    assert _version(path) == 2


def _create_reviews_v2(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE reviews (
                kind TEXT NOT NULL,
                period TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                payload TEXT NOT NULL,
                telegram_caption TEXT NOT NULL,
                reflection_question TEXT NOT NULL,
                safety_note TEXT,
                image_path TEXT,
                image_alt TEXT,
                language TEXT NOT NULL,
                model TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                PRIMARY KEY (kind, period)
            );
            CREATE TABLE review_sources (
                kind TEXT NOT NULL,
                period TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                label TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (kind, period, position)
            );
            CREATE TABLE corpus_documents (
                document_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                path TEXT NOT NULL,
                heading TEXT,
                text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                document_date TEXT,
                first_seen TEXT NOT NULL
            );
            CREATE TABLE generation_jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                period TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                UNIQUE (kind, period, source_hash, reason)
            );
            CREATE TABLE telegram_deliveries (
                kind TEXT NOT NULL,
                period TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (kind, period, chat_id)
            );
            CREATE TABLE corpus_embeddings (
                document_id TEXT PRIMARY KEY,
                embedding TEXT NOT NULL
            );
            PRAGMA user_version = 2;
            """
        )
        connection.execute(
            """
            INSERT INTO reviews VALUES (
                'week', '2026-07-26', '2026-07-26', '2026-08-01',
                'ready', 'Preserved review', '{}', 'Caption', 'Question?',
                NULL, NULL, NULL, 'EN', 'legacy/model', 'source-v1'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO generation_jobs
                (kind, period, source_hash, reason, status)
            VALUES ('week', '2026-07-26', 'source-v1', 'backfill', 'pending')
            """
        )


@pytest.mark.parametrize("fault_step", REVIEW_V2_STEPS)
def test_AC_5_4_review_migration_resumes_after_every_fault(
    tmp_path: Path,
    fault_step: str,
):
    directory = tmp_path / fault_step
    directory.mkdir()
    path = directory / "reviews.sqlite3"
    _create_reviews_v2(path)

    with pytest.raises(SimulatedMigrationCrash, match=fault_step):
        ReviewStore(
            path,
            embeddings_db_path=directory / "embeddings.sqlite3",
            migration_hook=_crash_at(fault_step),
        )

    store = ReviewStore(path, embeddings_db_path=directory / "embeddings.sqlite3")
    review = store.get_review("week", "2026-07-26")
    assert review is not None and review.title == "Preserved review"
    job = store.list_jobs()[0]
    assert job.attempt_count == 0 and job.next_attempt_at is None
    assert "corpus_embeddings" not in {
        row[0]
        for row in sqlite3.connect(path).execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert _version(path) == 3


def test_EC_5_1_all_migrations_are_idempotent_after_success(tmp_path: Path):
    semantic_path = tmp_path / "semantic.sqlite3"
    _create_semantic_v1(semantic_path)
    SemanticEmbeddingStore(semantic_path)

    enrichment_path = tmp_path / "enrichment.sqlite3"
    _create_legacy_enrichment(enrichment_path)
    EnrichmentStore(enrichment_path)
    assert drop_legacy_embedding_column(enrichment_path) is True

    reviews_path = tmp_path / "reviews.sqlite3"
    _create_reviews_v2(reviews_path)
    ReviewStore(reviews_path, embeddings_db_path=semantic_path)

    before = {
        "semantic": _fingerprint(semantic_path),
        "enrichment": _fingerprint(enrichment_path),
        "reviews": _fingerprint(reviews_path),
    }

    SemanticEmbeddingStore(semantic_path)
    EnrichmentStore(enrichment_path)
    assert drop_legacy_embedding_column(enrichment_path) is False
    ReviewStore(reviews_path, embeddings_db_path=semantic_path)

    assert _fingerprint(semantic_path) == before["semantic"]
    assert _fingerprint(enrichment_path) == before["enrichment"]
    assert _fingerprint(reviews_path) == before["reviews"]


@pytest.mark.parametrize(
    ("name", "future_version", "open_store"),
    [
        (
            "semantic",
            3,
            lambda path, sibling: SemanticEmbeddingStore(path),
        ),
        (
            "enrichment",
            3,
            lambda path, sibling: EnrichmentStore(path),
        ),
        (
            "reviews",
            4,
            lambda path, sibling: ReviewStore(
                path,
                embeddings_db_path=sibling,
            ),
        ),
    ],
)
def test_ERR_5_1_future_user_version_fails_without_writing_database(
    tmp_path: Path,
    name: str,
    future_version: int,
    open_store,
):
    path = tmp_path / f"{name}.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('preserve-me')")
        connection.execute(f"PRAGMA user_version = {future_version}")
    before = path.read_bytes()

    with pytest.raises(RuntimeError, match="Unsupported"):
        open_store(path, tmp_path / "separate-semantic.sqlite3")

    assert path.read_bytes() == before


def test_ERR_5_1_future_enrichment_version_blocks_legacy_drop_without_writes(
    tmp_path: Path,
):
    path = tmp_path / "enrichment.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE notes (embedding TEXT NULL)")
        connection.execute("PRAGMA user_version = 3")
    before = path.read_bytes()

    with pytest.raises(RuntimeError, match="Unsupported"):
        drop_legacy_embedding_column(path)

    assert path.read_bytes() == before
