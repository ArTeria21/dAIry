from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

from openai import AsyncOpenAI

from dairy_bot.config import Settings
from dairy_bot.services.diary_corpus import scan_diary_corpus
from dairy_bot.services.enrichment_db import drop_legacy_embedding_column


E5_MODEL = "intfloat/multilingual-e5-large"
E5_RECIPE_VERSION = "e5-query-passage-v1"
RAW_RECIPE_VERSION = "raw-v1"
RETRY_DELAY = timedelta(minutes=15)
MAX_BATCH_SIZE = 32
SEMANTIC_SCHEMA_VERSION = 2
logger = logging.getLogger(__name__)

Embed = Callable[[str], Awaitable[Sequence[float]]]
EmbedMany = Callable[[Sequence[str]], Awaitable[Sequence[Sequence[float]]]]
_PROCESS_LOCKS: dict[tuple[int, str, str, str], asyncio.Lock] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SemanticDocument(Protocol):
    document_id: str
    content_hash: str
    text: str


@dataclass(frozen=True, slots=True)
class StoredEmbedding:
    document_id: str
    content_hash: str
    model: str
    recipe_version: str
    dimension: int
    embedding: tuple[float, ...]
    updated_at: datetime
    generation_id: int | None = None


@dataclass(frozen=True, slots=True)
class SemanticGeneration:
    generation_id: int
    status: str
    corpus_hash: str
    model: str
    recipe_version: str
    dimension: int | None
    next_retry_at: datetime | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class SemanticIndexState:
    status: str
    corpus_hash: str
    model: str
    recipe_version: str
    next_retry_at: datetime | None
    updated_at: datetime
    active_generation_id: int | None = None
    previous_generation_id: int | None = None
    building_generation_id: int | None = None


class InvalidEmbeddingResponse(ValueError):
    """The provider response cannot become part of a published generation."""


class SemanticIndexUnavailable(RuntimeError):
    """No published generation matches the consumer's retrieval contract."""


class SemanticEmbeddingStore:
    """Versioned bot-owned repository for immutable diary-vector snapshots."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        migration_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._migration_hook = migration_hook
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _checkpoint(self, step: str) -> None:
        if self._migration_hook is not None:
            self._migration_hook(step)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SEMANTIC_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Unsupported semantic database version: {version}"
                )
            if version == SEMANTIC_SCHEMA_VERSION:
                return
            connection.execute("BEGIN IMMEDIATE")
            if version == 1:
                self._migrate_v1(connection)
            else:
                self._create_schema(connection)
            connection.execute(f"PRAGMA user_version = {SEMANTIC_SCHEMA_VERSION}")
            self._checkpoint("set_user_version")

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_generations (
                generation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                corpus_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                recipe_version TEXT NOT NULL,
                dimension INTEGER NULL,
                next_retry_at TEXT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                published_at TEXT NULL
            )
            """
        )
        self._checkpoint("create_generations")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_vectors (
                generation_id INTEGER NOT NULL,
                document_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                embedding TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (generation_id, document_id),
                FOREIGN KEY (generation_id)
                    REFERENCES semantic_generations(generation_id)
                    ON DELETE CASCADE
            )
            """
        )
        self._checkpoint("create_vectors")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_index_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                active_generation_id INTEGER NULL,
                previous_generation_id INTEGER NULL,
                building_generation_id INTEGER NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._checkpoint("create_index_state")
        connection.execute(
            """
            INSERT OR IGNORE INTO semantic_index_state (
                singleton, active_generation_id, previous_generation_id,
                building_generation_id, updated_at
            ) VALUES (1, NULL, NULL, NULL, ?)
            """,
            (_utc_now().isoformat(),),
        )
        self._checkpoint("seed_index_state")

    def _migrate_v1(self, connection: sqlite3.Connection) -> None:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if (
            "semantic_embeddings" not in tables
            and "semantic_embeddings_v1" not in tables
        ):
            self._create_schema(connection)
            return

        if "semantic_embeddings" in tables:
            connection.execute(
                "ALTER TABLE semantic_embeddings RENAME TO semantic_embeddings_v1"
            )
            self._checkpoint("rename_legacy_vectors")
        if "semantic_index_state" in tables:
            connection.execute(
                "ALTER TABLE semantic_index_state RENAME TO semantic_index_state_v1"
            )
            self._checkpoint("rename_legacy_state")
        elif "semantic_index_state_v1" not in tables:
            raise RuntimeError("Semantic v1 index state is missing")

        current_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "semantic_generations" in current_tables:
            connection.execute("DELETE FROM semantic_vectors")
            connection.execute("DELETE FROM semantic_generations")
        if "semantic_index_state" in current_tables:
            connection.execute("DELETE FROM semantic_index_state")
        self._create_schema(connection)

        state = connection.execute(
            "SELECT * FROM semantic_index_state_v1 WHERE singleton = 1"
        ).fetchone()
        rows = connection.execute(
            "SELECT * FROM semantic_embeddings_v1 ORDER BY document_id"
        ).fetchall()
        if state is not None:
            now = str(state["updated_at"] or _utc_now().isoformat())
            status = "ready" if str(state["status"]) == "ready" else "building"
            vectors: list[tuple[sqlite3.Row, tuple[float, ...]]] = []
            for row in rows:
                try:
                    values = _validated_vector(json.loads(str(row["embedding"])))
                except (InvalidEmbeddingResponse, TypeError, ValueError):
                    continue
                vectors.append((row, values))
            dimensions = {len(values) for _, values in vectors}
            dimension = next(iter(dimensions)) if len(dimensions) == 1 else None
            cursor = connection.execute(
                """
                INSERT INTO semantic_generations (
                    status, corpus_hash, model, recipe_version, dimension,
                    next_retry_at, created_at, updated_at, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    status,
                    str(state["corpus_hash"]),
                    str(state["model"]),
                    str(state["recipe_version"]),
                    dimension,
                    state["next_retry_at"],
                    now,
                    now,
                    now if status == "ready" else None,
                ),
            )
            self._checkpoint("copy_generation")
            generation_id = int(cursor.lastrowid)
            if dimension is not None:
                connection.executemany(
                    """
                    INSERT INTO semantic_vectors (
                        generation_id, document_id, content_hash, dimension,
                        embedding, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            generation_id,
                            str(row["document_id"]),
                            str(row["content_hash"]),
                            len(values),
                            json.dumps(values),
                            str(row["updated_at"]),
                        )
                        for row, values in vectors
                        if len(values) == dimension
                    ],
                )
            self._checkpoint("copy_vectors")
            connection.execute(
                """
                UPDATE semantic_index_state
                SET active_generation_id = ?, building_generation_id = ?,
                    updated_at = ?
                WHERE singleton = 1
                """,
                (
                    generation_id if status == "ready" else None,
                    generation_id if status == "building" else None,
                    now,
                ),
            )
            self._checkpoint("copy_index_state")

        connection.execute("DROP TABLE semantic_embeddings_v1")
        self._checkpoint("drop_legacy_vectors")
        connection.execute("DROP TABLE semantic_index_state_v1")
        self._checkpoint("drop_legacy_state")

    def get_active_generation(self) -> SemanticGeneration | None:
        return self._pointer_generation("active_generation_id")

    def get_previous_generation(self) -> SemanticGeneration | None:
        return self._pointer_generation("previous_generation_id")

    def get_building_generation(self) -> SemanticGeneration | None:
        return self._pointer_generation("building_generation_id")

    def _pointer_generation(self, column: str) -> SemanticGeneration | None:
        if column not in {
            "active_generation_id",
            "previous_generation_id",
            "building_generation_id",
        }:
            raise ValueError("Unsupported semantic generation pointer")
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT generation.*
                FROM semantic_index_state AS state
                LEFT JOIN semantic_generations AS generation
                  ON generation.generation_id = state.{column}
                WHERE state.singleton = 1
                """
            ).fetchone()
        if row is None or row["generation_id"] is None:
            return None
        return _generation_from_row(row)

    def get_state(self) -> SemanticIndexState:
        with self._connection() as connection:
            pointers = connection.execute(
                "SELECT * FROM semantic_index_state WHERE singleton = 1"
            ).fetchone()
            if pointers is None:
                raise RuntimeError("Semantic index state is missing")
            active = _generation_row(connection, pointers["active_generation_id"])
            building = _generation_row(connection, pointers["building_generation_id"])

        selected = active if active is not None else building
        status = "building" if building is not None else "ready" if active else "building"
        return SemanticIndexState(
            status=status,
            corpus_hash="" if selected is None else str(selected["corpus_hash"]),
            model="" if selected is None else str(selected["model"]),
            recipe_version=(
                "" if selected is None else str(selected["recipe_version"])
            ),
            next_retry_at=(
                None
                if building is None or building["next_retry_at"] is None
                else datetime.fromisoformat(str(building["next_retry_at"]))
            ),
            updated_at=datetime.fromisoformat(str(pointers["updated_at"])),
            active_generation_id=_optional_int(pointers["active_generation_id"]),
            previous_generation_id=_optional_int(
                pointers["previous_generation_id"]
            ),
            building_generation_id=_optional_int(
                pointers["building_generation_id"]
            ),
        )

    def get_embedding(
        self,
        document_id: str,
        *,
        content_hash: str,
        model: str,
        recipe_version: str,
        generation_id: int | None = None,
    ) -> StoredEmbedding | None:
        with self._connection() as connection:
            selected_generation = generation_id
            if selected_generation is None:
                pointer = connection.execute(
                    """
                    SELECT active_generation_id FROM semantic_index_state
                    WHERE singleton = 1
                    """
                ).fetchone()
                selected_generation = (
                    None if pointer is None else pointer["active_generation_id"]
                )
            if selected_generation is None:
                return None
            row = connection.execute(
                """
                SELECT vectors.*, generations.model, generations.recipe_version
                FROM semantic_vectors AS vectors
                JOIN semantic_generations AS generations
                  ON generations.generation_id = vectors.generation_id
                WHERE vectors.generation_id = ?
                  AND vectors.document_id = ?
                  AND vectors.content_hash = ?
                  AND generations.model = ?
                  AND generations.recipe_version = ?
                """,
                (
                    selected_generation,
                    document_id,
                    content_hash,
                    model,
                    recipe_version,
                ),
            ).fetchone()
        return None if row is None else _embedding_from_row(row)

    def list_embeddings(
        self,
        *,
        generation_id: int | None = None,
    ) -> list[StoredEmbedding]:
        with self._connection() as connection:
            selected_generation = generation_id
            if selected_generation is None:
                pointer = connection.execute(
                    """
                    SELECT active_generation_id FROM semantic_index_state
                    WHERE singleton = 1
                    """
                ).fetchone()
                selected_generation = (
                    None if pointer is None else pointer["active_generation_id"]
                )
            if selected_generation is None:
                return []
            rows = connection.execute(
                """
                SELECT vectors.*, generations.model, generations.recipe_version
                FROM semantic_vectors AS vectors
                JOIN semantic_generations AS generations
                  ON generations.generation_id = vectors.generation_id
                WHERE vectors.generation_id = ?
                ORDER BY vectors.document_id
                """,
                (selected_generation,),
            ).fetchall()
        return [_embedding_from_row(row) for row in rows]

    def import_legacy_embeddings(
        self,
        enrichment_db_path: str | Path,
        documents: Sequence[SemanticDocument],
        *,
        model: str,
    ) -> int:
        if self.get_active_generation() is not None:
            return 0
        path = Path(enrichment_db_path)
        if not path.exists():
            return 0
        try:
            with sqlite3.connect(path) as legacy:
                legacy.row_factory = sqlite3.Row
                columns = {
                    str(row[1])
                    for row in legacy.execute("PRAGMA table_info(notes)").fetchall()
                }
                if "embedding" not in columns:
                    return 0
                rows = legacy.execute(
                    """
                    SELECT notes.id, notes.embedding, state.content_hash
                    FROM notes
                    JOIN note_entry_state AS state ON state.id = notes.id
                    WHERE notes.embedding IS NOT NULL
                    """
                ).fetchall()
        except sqlite3.Error:
            logger.exception("Could not inspect legacy enrichment embeddings")
            return 0

        by_id = {
            document.document_id.removeprefix("diary:"): document
            for document in documents
        }
        compatible: list[tuple[SemanticDocument, tuple[float, ...]]] = []
        for row in rows:
            document = by_id.get(str(row["id"]))
            if document is None or str(row["content_hash"]) != document.content_hash:
                logger.info("Skipping stale legacy vector for %s", row["id"])
                continue
            try:
                values = _validated_vector(json.loads(str(row["embedding"])))
            except (InvalidEmbeddingResponse, TypeError, ValueError):
                logger.warning("Skipping invalid legacy vector for %s", row["id"])
                continue
            compatible.append((document, values))
        dimensions = {len(values) for _, values in compatible}
        if not compatible or len(dimensions) != 1:
            return 0

        now = _utc_now().isoformat()
        dimension = next(iter(dimensions))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT active_generation_id FROM semantic_index_state WHERE singleton = 1"
            ).fetchone()
            if state is None or state["active_generation_id"] is not None:
                return 0
            cursor = connection.execute(
                """
                INSERT INTO semantic_generations (
                    status, corpus_hash, model, recipe_version, dimension,
                    next_retry_at, created_at, updated_at, published_at
                ) VALUES ('ready', ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    corpus_hash(documents),
                    model,
                    RAW_RECIPE_VERSION,
                    dimension,
                    now,
                    now,
                    now,
                ),
            )
            generation_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO semantic_vectors (
                    generation_id, document_id, content_hash, dimension,
                    embedding, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        generation_id,
                        document.document_id,
                        document.content_hash,
                        dimension,
                        json.dumps(values),
                        now,
                    )
                    for document, values in compatible
                ],
            )
            connection.execute(
                """
                UPDATE semantic_index_state
                SET active_generation_id = ?, previous_generation_id = NULL,
                    building_generation_id = NULL, updated_at = ?
                WHERE singleton = 1
                """,
                (generation_id, now),
            )
        return len(compatible)

    def ensure_building_generation(
        self,
        *,
        corpus_hash: str,
        model: str,
        recipe_version: str,
        now: datetime,
    ) -> SemanticGeneration:
        timestamp = now.isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT * FROM semantic_index_state WHERE singleton = 1"
            ).fetchone()
            if state is None:
                raise RuntimeError("Semantic index state is missing")
            building = _generation_row(connection, state["building_generation_id"])
            if (
                building is not None
                and str(building["model"]) == model
                and str(building["recipe_version"]) == recipe_version
            ):
                if (
                    str(building["corpus_hash"]) != corpus_hash
                    or building["next_retry_at"] is not None
                ):
                    connection.execute(
                        """
                        UPDATE semantic_generations
                        SET corpus_hash = ?, next_retry_at = NULL, updated_at = ?
                        WHERE generation_id = ?
                        """,
                        (corpus_hash, timestamp, building["generation_id"]),
                    )
                    connection.execute(
                        "UPDATE semantic_index_state SET updated_at = ? WHERE singleton = 1",
                        (timestamp,),
                    )
                row = _generation_row(connection, building["generation_id"])
                if row is None:
                    raise RuntimeError("Building semantic generation disappeared")
                return _generation_from_row(row)

            if building is not None:
                connection.execute(
                    "DELETE FROM semantic_generations WHERE generation_id = ?",
                    (building["generation_id"],),
                )
            cursor = connection.execute(
                """
                INSERT INTO semantic_generations (
                    status, corpus_hash, model, recipe_version, dimension,
                    next_retry_at, created_at, updated_at, published_at
                ) VALUES ('building', ?, ?, ?, NULL, NULL, ?, ?, NULL)
                """,
                (corpus_hash, model, recipe_version, timestamp, timestamp),
            )
            generation_id = int(cursor.lastrowid)
            active = _generation_row(connection, state["active_generation_id"])
            if (
                active is not None
                and str(active["model"]) == model
                and str(active["recipe_version"]) == recipe_version
            ):
                connection.execute(
                    """
                    INSERT INTO semantic_vectors (
                        generation_id, document_id, content_hash, dimension,
                        embedding, updated_at
                    )
                    SELECT ?, document_id, content_hash, dimension,
                           embedding, updated_at
                    FROM semantic_vectors
                    WHERE generation_id = ?
                    """,
                    (generation_id, active["generation_id"]),
                )
            connection.execute(
                """
                UPDATE semantic_index_state
                SET building_generation_id = ?, updated_at = ?
                WHERE singleton = 1
                """,
                (generation_id, timestamp),
            )
            row = _generation_row(connection, generation_id)
        if row is None:
            raise RuntimeError("Could not create semantic generation")
        return _generation_from_row(row)

    def upsert_generation_embeddings(
        self,
        generation_id: int,
        items: Sequence[tuple[SemanticDocument, Sequence[float]]],
        *,
        updated_at: datetime | None = None,
    ) -> None:
        validated = [
            (document, _validated_vector(values)) for document, values in items
        ]
        dimensions = {len(values) for _, values in validated}
        if len(dimensions) > 1:
            raise InvalidEmbeddingResponse("Embedding dimensions do not match")
        timestamp = (updated_at or _utc_now()).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = _generation_row(connection, generation_id)
            if generation is None or str(generation["status"]) != "building":
                raise RuntimeError("Semantic generation is not writable")
            existing_dimensions = {
                int(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT dimension FROM semantic_vectors
                    WHERE generation_id = ?
                    """,
                    (generation_id,),
                ).fetchall()
            }
            if dimensions and existing_dimensions and dimensions != existing_dimensions:
                raise InvalidEmbeddingResponse("Embedding dimensions do not match")
            connection.executemany(
                """
                INSERT INTO semantic_vectors (
                    generation_id, document_id, content_hash, dimension,
                    embedding, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(generation_id, document_id) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    dimension = excluded.dimension,
                    embedding = excluded.embedding,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        generation_id,
                        document.document_id,
                        document.content_hash,
                        len(values),
                        json.dumps(values),
                        timestamp,
                    )
                    for document, values in validated
                ],
            )

    def mark_generation_retry(
        self,
        generation_id: int,
        *,
        next_retry_at: datetime,
    ) -> None:
        now = _utc_now().isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE semantic_generations
                SET status = 'building', next_retry_at = ?, updated_at = ?
                WHERE generation_id = ?
                """,
                (next_retry_at.isoformat(), now, generation_id),
            )
            connection.execute(
                "UPDATE semantic_index_state SET updated_at = ? WHERE singleton = 1",
                (now,),
            )

    def publish_generation(
        self,
        generation_id: int,
        *,
        documents: Sequence[SemanticDocument],
        corpus_hash: str,
        published_at: datetime,
    ) -> None:
        timestamp = published_at.isoformat()
        expected = {item.document_id: item.content_hash for item in documents}
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT * FROM semantic_index_state WHERE singleton = 1"
            ).fetchone()
            generation = _generation_row(connection, generation_id)
            if state is None or generation is None:
                raise RuntimeError("Semantic generation is missing")
            if state["building_generation_id"] != generation_id:
                raise RuntimeError("Semantic generation was superseded")

            if expected:
                placeholders = ",".join("?" for _ in expected)
                connection.execute(
                    f"""
                    DELETE FROM semantic_vectors
                    WHERE generation_id = ?
                      AND document_id NOT IN ({placeholders})
                    """,
                    (generation_id, *sorted(expected)),
                )
            else:
                connection.execute(
                    "DELETE FROM semantic_vectors WHERE generation_id = ?",
                    (generation_id,),
                )
            rows = connection.execute(
                """
                SELECT * FROM semantic_vectors
                WHERE generation_id = ? ORDER BY document_id
                """,
                (generation_id,),
            ).fetchall()
            if len(rows) != len(expected):
                raise InvalidEmbeddingResponse("Embedding response count does not match")
            dimensions: set[int] = set()
            for row in rows:
                document_id = str(row["document_id"])
                if expected.get(document_id) != str(row["content_hash"]):
                    raise InvalidEmbeddingResponse("Embedding content hash is stale")
                values = _validated_vector(json.loads(str(row["embedding"])))
                if len(values) != int(row["dimension"]):
                    raise InvalidEmbeddingResponse("Embedding dimension metadata is stale")
                dimensions.add(len(values))
            if len(dimensions) > 1:
                raise InvalidEmbeddingResponse("Embedding dimensions do not match")
            dimension = next(iter(dimensions)) if dimensions else 0

            old_active = _optional_int(state["active_generation_id"])
            connection.execute(
                """
                UPDATE semantic_generations
                SET status = 'ready', corpus_hash = ?, dimension = ?,
                    next_retry_at = NULL, updated_at = ?, published_at = ?
                WHERE generation_id = ?
                """,
                (corpus_hash, dimension, timestamp, timestamp, generation_id),
            )
            connection.execute(
                """
                UPDATE semantic_index_state
                SET active_generation_id = ?, previous_generation_id = ?,
                    building_generation_id = NULL, updated_at = ?
                WHERE singleton = 1
                """,
                (generation_id, old_active, timestamp),
            )
            retained = [generation_id]
            if old_active is not None and old_active != generation_id:
                retained.append(old_active)
            placeholders = ",".join("?" for _ in retained)
            connection.execute(
                f"""
                DELETE FROM semantic_generations
                WHERE generation_id NOT IN ({placeholders})
                """,
                retained,
            )

    def upsert_embedding(
        self,
        *,
        document_id: str,
        content_hash: str,
        model: str,
        recipe_version: str,
        embedding: Sequence[float],
        updated_at: datetime | None = None,
    ) -> StoredEmbedding:
        """Compatibility writer that still publishes through a new generation."""
        values = _validated_vector(embedding)
        timestamp = (updated_at or _utc_now()).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT * FROM semantic_index_state WHERE singleton = 1"
            ).fetchone()
            if state is None:
                raise RuntimeError("Semantic index state is missing")
            active = _generation_row(connection, state["active_generation_id"])
            existing = None
            if (
                active is not None
                and str(active["model"]) == model
                and str(active["recipe_version"]) == recipe_version
            ):
                existing = connection.execute(
                    """
                    SELECT vectors.*, generations.model, generations.recipe_version
                    FROM semantic_vectors AS vectors
                    JOIN semantic_generations AS generations
                      ON generations.generation_id = vectors.generation_id
                    WHERE vectors.generation_id = ? AND vectors.document_id = ?
                      AND vectors.content_hash = ?
                    """,
                    (active["generation_id"], document_id, content_hash),
                ).fetchone()
            if existing is not None:
                return _embedding_from_row(existing)

            cursor = connection.execute(
                """
                INSERT INTO semantic_generations (
                    status, corpus_hash, model, recipe_version, dimension,
                    next_retry_at, created_at, updated_at, published_at
                ) VALUES ('ready', '', ?, ?, ?, NULL, ?, ?, ?)
                """,
                (model, recipe_version, len(values), timestamp, timestamp, timestamp),
            )
            generation_id = int(cursor.lastrowid)
            if (
                active is not None
                and str(active["model"]) == model
                and str(active["recipe_version"]) == recipe_version
            ):
                dimensions = {
                    int(row[0])
                    for row in connection.execute(
                        """
                        SELECT DISTINCT dimension FROM semantic_vectors
                        WHERE generation_id = ?
                        """,
                        (active["generation_id"],),
                    ).fetchall()
                }
                if dimensions and dimensions != {len(values)}:
                    raise InvalidEmbeddingResponse("Embedding dimensions do not match")
                connection.execute(
                    """
                    INSERT INTO semantic_vectors (
                        generation_id, document_id, content_hash, dimension,
                        embedding, updated_at
                    )
                    SELECT ?, document_id, content_hash, dimension,
                           embedding, updated_at
                    FROM semantic_vectors WHERE generation_id = ?
                    """,
                    (generation_id, active["generation_id"]),
                )
            connection.execute(
                """
                INSERT INTO semantic_vectors (
                    generation_id, document_id, content_hash, dimension,
                    embedding, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(generation_id, document_id) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    dimension = excluded.dimension,
                    embedding = excluded.embedding,
                    updated_at = excluded.updated_at
                """,
                (
                    generation_id,
                    document_id,
                    content_hash,
                    len(values),
                    json.dumps(values),
                    timestamp,
                ),
            )
            pairs = connection.execute(
                """
                SELECT document_id, content_hash FROM semantic_vectors
                WHERE generation_id = ? ORDER BY document_id
                """,
                (generation_id,),
            ).fetchall()
            generation_hash = _corpus_hash_pairs(
                [(str(row[0]), str(row[1])) for row in pairs]
            )
            connection.execute(
                "UPDATE semantic_generations SET corpus_hash = ? WHERE generation_id = ?",
                (generation_hash, generation_id),
            )
            old_active = _optional_int(state["active_generation_id"])
            connection.execute(
                """
                UPDATE semantic_index_state
                SET active_generation_id = ?, previous_generation_id = ?,
                    building_generation_id = NULL, updated_at = ?
                WHERE singleton = 1
                """,
                (generation_id, old_active, timestamp),
            )
            retained = [generation_id]
            if old_active is not None:
                retained.append(old_active)
            placeholders = ",".join("?" for _ in retained)
            connection.execute(
                f"DELETE FROM semantic_generations WHERE generation_id NOT IN ({placeholders})",
                retained,
            )
        return StoredEmbedding(
            document_id=document_id,
            content_hash=content_hash,
            model=model,
            recipe_version=recipe_version,
            dimension=len(values),
            embedding=values,
            updated_at=datetime.fromisoformat(timestamp),
            generation_id=generation_id,
        )


class SemanticEmbeddingService:
    """Role-aware provider adapter backed by generation-specific cache rows."""

    def __init__(
        self,
        *,
        store: SemanticEmbeddingStore,
        model: str,
        embed: Embed | None = None,
        embed_many: EmbedMany | None = None,
    ) -> None:
        if embed is None and embed_many is None:
            raise ValueError("An embedding provider is required")
        self.store = store
        self.model = model
        self.recipe_version = recipe_for_model(model)
        self._embed_one = embed
        self._embed_many = embed_many
        self.embed: Embed = embed or self._single_from_batch

    async def _single_from_batch(self, text: str) -> Sequence[float]:
        if self._embed_many is None:
            raise RuntimeError("Batch embedding provider is missing")
        response = list(await self._embed_many([text]))
        if len(response) != 1:
            raise InvalidEmbeddingResponse("Embedding response count does not match")
        return response[0]

    async def embed_document(self, document: SemanticDocument) -> tuple[float, ...]:
        cached = self.store.get_embedding(
            document.document_id,
            content_hash=document.content_hash,
            model=self.model,
            recipe_version=self.recipe_version,
        )
        if cached is not None:
            return cached.embedding
        values = _validated_vector(
            await self.embed(_document_input(self.model, document.text))
        )
        return self.store.upsert_embedding(
            document_id=document.document_id,
            content_hash=document.content_hash,
            model=self.model,
            recipe_version=self.recipe_version,
            embedding=values,
        ).embedding

    async def embed_documents(
        self,
        documents: Sequence[SemanticDocument],
        *,
        generation_id: int,
    ) -> None:
        missing = [
            document
            for document in documents
            if self.store.get_embedding(
                document.document_id,
                content_hash=document.content_hash,
                model=self.model,
                recipe_version=self.recipe_version,
                generation_id=generation_id,
            )
            is None
        ]
        if not missing:
            return
        if self._embed_many is not None:
            inputs = [_document_input(self.model, document.text) for document in missing]
            response = list(await self._embed_many(inputs))
            if len(response) != len(missing):
                raise InvalidEmbeddingResponse("Embedding response count does not match")
            values = [_validated_vector(item) for item in response]
            if len({len(item) for item in values}) != 1:
                raise InvalidEmbeddingResponse("Embedding dimensions do not match")
            self.store.upsert_generation_embeddings(
                generation_id,
                list(zip(missing, values, strict=True)),
            )
            return

        for document in missing:
            cached = self.store.get_embedding(
                document.document_id,
                content_hash=document.content_hash,
                model=self.model,
                recipe_version=self.recipe_version,
                generation_id=generation_id,
            )
            if cached is not None:
                continue
            values = _validated_vector(
                await self.embed(_document_input(self.model, document.text))
            )
            self.store.upsert_generation_embeddings(
                generation_id,
                [(document, values)],
            )

    async def embed_query(self, query: str) -> tuple[float, ...]:
        active = self.store.get_active_generation()
        active_recipe = (
            active.recipe_version
            if active is not None and active.model == self.model
            else self.recipe_version
        )
        text = _query_input(active_recipe, query)
        if self._embed_many is not None:
            response = list(await self._embed_many([text]))
            if len(response) != 1:
                raise InvalidEmbeddingResponse("Embedding response count does not match")
            return _validated_vector(response[0])
        return _validated_vector(await self.embed(text))


class DiarySemanticIndexer:
    """Resumable full-corpus builder with atomic generation publication."""

    def __init__(
        self,
        *,
        store: SemanticEmbeddingStore,
        embeddings: SemanticEmbeddingService,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = _utc_now,
        legacy_enrichment_db_path: str | Path | None = None,
    ) -> None:
        self.store = store
        self.embeddings = embeddings
        self.sleep = sleep
        self.now = now
        self.legacy_enrichment_db_path = (
            None
            if legacy_enrichment_db_path is None
            else Path(legacy_enrichment_db_path)
        )

    async def sync(self, documents: Sequence[SemanticDocument]) -> bool:
        key = (
            id(asyncio.get_running_loop()),
            str(self.store.db_path.resolve()),
            self.embeddings.model,
            self.embeddings.recipe_version,
        )
        lock = _PROCESS_LOCKS.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._sync(documents)

    async def _sync(self, documents: Sequence[SemanticDocument]) -> bool:
        current = self.now()
        ordered = sorted(documents, key=lambda item: item.document_id)
        target_hash = corpus_hash(ordered)
        if self.legacy_enrichment_db_path is not None:
            self.store.import_legacy_embeddings(
                self.legacy_enrichment_db_path,
                ordered,
                model=self.embeddings.model,
            )
        active = self.store.get_active_generation()
        building = self.store.get_building_generation()
        if building is not None and building.next_retry_at is not None:
            if current < building.next_retry_at:
                return False
        if (
            active is not None
            and building is None
            and active.corpus_hash == target_hash
            and active.model == self.embeddings.model
            and active.recipe_version == self.embeddings.recipe_version
        ):
            return True

        generation = self.store.ensure_building_generation(
            corpus_hash=target_hash,
            model=self.embeddings.model,
            recipe_version=self.embeddings.recipe_version,
            now=current,
        )
        missing = [
            document
            for document in ordered
            if self.store.get_embedding(
                document.document_id,
                content_hash=document.content_hash,
                model=self.embeddings.model,
                recipe_version=self.embeddings.recipe_version,
                generation_id=generation.generation_id,
            )
            is None
        ]
        for offset in range(0, len(missing), MAX_BATCH_SIZE):
            batch = missing[offset : offset + MAX_BATCH_SIZE]
            for attempt in range(3):
                try:
                    await self.embeddings.embed_documents(
                        batch,
                        generation_id=generation.generation_id,
                    )
                    break
                except Exception:
                    if attempt == 2:
                        self.store.mark_generation_retry(
                            generation.generation_id,
                            next_retry_at=current + RETRY_DELAY,
                        )
                        return False
                    await self.sleep(float(2**attempt))

        try:
            self.store.publish_generation(
                generation.generation_id,
                documents=ordered,
                corpus_hash=target_hash,
                published_at=current,
            )
        except InvalidEmbeddingResponse:
            self.store.mark_generation_retry(
                generation.generation_id,
                next_retry_at=current + RETRY_DELAY,
            )
            return False

        if (
            self.legacy_enrichment_db_path is not None
            and self.embeddings.recipe_version == E5_RECIPE_VERSION
        ):
            try:
                drop_legacy_embedding_column(self.legacy_enrichment_db_path)
            except sqlite3.Error:
                logger.exception("Could not finalize legacy embedding migration")
        return True


@dataclass(slots=True)
class SemanticRuntime:
    store: SemanticEmbeddingStore
    embeddings: SemanticEmbeddingService
    indexer: DiarySemanticIndexer
    openai_client: Any
    legacy_enrichment_db_path: Path | None = None
    _closed: bool = False

    async def sync_vault(self, vault: Path, *, first_seen: datetime) -> bool:
        return await self.indexer.sync(
            scan_diary_corpus(vault, first_seen=first_seen)
        )

    async def run_forever(
        self,
        vault: Path,
        *,
        local_timezone: Any,
        poll_interval_seconds: float = 60.0,
    ) -> None:
        while True:
            try:
                await self.sync_vault(
                    vault,
                    first_seen=datetime.now(local_timezone),
                )
            except Exception:
                logger.exception("Semantic index reconciliation failed; retrying later")
            await asyncio.sleep(poll_interval_seconds)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self.openai_client, "close", None)
        if close is not None:
            await close()


def build_semantic_runtime(
    settings: Settings,
    *,
    openai_client: Any | None = None,
) -> SemanticRuntime | None:
    if not settings.enrichment_enabled and not settings.reviews_enabled:
        return None
    openai = openai_client or AsyncOpenAI(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
    )

    async def embed(text: str) -> list[float]:
        response = await openai.embeddings.create(
            model=settings.embedding_model_name,
            input=text,
            encoding_format="float",
        )
        return list(response.data[0].embedding)

    async def embed_many(inputs: Sequence[str]) -> list[list[float]]:
        if len(inputs) == 1:
            return [await embed(inputs[0])]
        response = await openai.embeddings.create(
            model=settings.embedding_model_name,
            input=list(inputs),
            encoding_format="float",
        )
        ordered = sorted(response.data, key=lambda item: getattr(item, "index", 0))
        return [list(item.embedding) for item in ordered]

    store = SemanticEmbeddingStore(settings.embeddings_db_path)
    embeddings = SemanticEmbeddingService(
        store=store,
        embed=embed,
        embed_many=embed_many,
        model=settings.embedding_model_name,
    )
    legacy_path = Path(settings.enrichment_db_path)
    return SemanticRuntime(
        store=store,
        embeddings=embeddings,
        indexer=DiarySemanticIndexer(
            store=store,
            embeddings=embeddings,
            legacy_enrichment_db_path=legacy_path,
        ),
        openai_client=openai,
        legacy_enrichment_db_path=legacy_path,
    )


def recipe_for_model(model: str) -> str:
    return E5_RECIPE_VERSION if model == E5_MODEL else RAW_RECIPE_VERSION


def corpus_hash(documents: Sequence[SemanticDocument]) -> str:
    return _corpus_hash_pairs(
        [
            (document.document_id, document.content_hash)
            for document in sorted(documents, key=lambda item: item.document_id)
        ]
    )


def _corpus_hash_pairs(pairs: Sequence[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for document_id, content_hash in pairs:
        digest.update(document_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content_hash.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _document_input(model: str, text: str) -> str:
    return f"passage: {text}" if model == E5_MODEL else text


def _query_input(recipe_version: str, text: str) -> str:
    return f"query: {text}" if recipe_version == E5_RECIPE_VERSION else text


def _validated_vector(values: Sequence[float]) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if not vector:
        raise InvalidEmbeddingResponse("Embedding vector is empty")
    if not all(math.isfinite(value) for value in vector):
        raise InvalidEmbeddingResponse("Embedding vector contains non-finite values")
    return vector


def _generation_row(
    connection: sqlite3.Connection,
    generation_id: object,
) -> sqlite3.Row | None:
    if generation_id is None:
        return None
    return connection.execute(
        "SELECT * FROM semantic_generations WHERE generation_id = ?",
        (generation_id,),
    ).fetchone()


def _generation_from_row(row: sqlite3.Row) -> SemanticGeneration:
    return SemanticGeneration(
        generation_id=int(row["generation_id"]),
        status=str(row["status"]),
        corpus_hash=str(row["corpus_hash"]),
        model=str(row["model"]),
        recipe_version=str(row["recipe_version"]),
        dimension=None if row["dimension"] is None else int(row["dimension"]),
        next_retry_at=(
            None
            if row["next_retry_at"] is None
            else datetime.fromisoformat(str(row["next_retry_at"]))
        ),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        published_at=(
            None
            if row["published_at"] is None
            else datetime.fromisoformat(str(row["published_at"]))
        ),
    )


def _embedding_from_row(row: sqlite3.Row) -> StoredEmbedding:
    return StoredEmbedding(
        document_id=str(row["document_id"]),
        content_hash=str(row["content_hash"]),
        model=str(row["model"]),
        recipe_version=str(row["recipe_version"]),
        dimension=int(row["dimension"]),
        embedding=tuple(float(value) for value in json.loads(row["embedding"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        generation_id=int(row["generation_id"]),
    )


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)
