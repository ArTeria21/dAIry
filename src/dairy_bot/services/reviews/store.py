from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Sequence

from dairy_bot.services.semantic_embeddings import (
    SemanticEmbeddingStore,
    recipe_for_model,
)

from .models import (
    CorpusDocument,
    GenerationJob,
    ReviewRecord,
    ReviewSource,
    TelegramDelivery,
)
from .retrieval import EmbeddedDocument


REVIEWS_SCHEMA_VERSION = 3


class ReviewStore:
    """Durable bot-owned state for review generation and delivery."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        embeddings_db_path: str | Path | None = None,
        semantic_store: SemanticEmbeddingStore | None = None,
        migration_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migration_hook = migration_hook
        self._migrate()
        self.embeddings = semantic_store or SemanticEmbeddingStore(
            embeddings_db_path or self.db_path.with_name("embeddings.sqlite3")
        )

    def _checkpoint(self, step: str) -> None:
        if self._migration_hook is not None:
            self._migration_hook(step)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
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
            if version > REVIEWS_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Unsupported reviews database version: {version}"
                )
            if version == REVIEWS_SCHEMA_VERSION:
                return

            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reviews (
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
                    retrieval_model TEXT NULL,
                    retrieval_recipe TEXT NULL,
                    PRIMARY KEY (kind, period)
                )
                """
            )
            self._checkpoint("create_reviews")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_sources (
                    kind TEXT NOT NULL,
                    period TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    label TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (kind, period, position),
                    FOREIGN KEY (kind, period) REFERENCES reviews(kind, period)
                        ON DELETE CASCADE
                )
                """
            )
            self._checkpoint("create_review_sources")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS review_sources_source_id_idx
                    ON review_sources(source_id)
                """
            )
            self._checkpoint("create_review_sources_index")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS corpus_documents (
                    document_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    heading TEXT,
                    text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    document_date TEXT,
                    first_seen TEXT NOT NULL
                )
                """
            )
            self._checkpoint("create_corpus_documents")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    period TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NULL,
                    last_error TEXT NULL,
                    UNIQUE (kind, period, source_hash, reason)
                )
                """
            )
            self._checkpoint("create_generation_jobs")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_deliveries (
                    kind TEXT NOT NULL,
                    period TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY (kind, period, chat_id)
                )
                """
            )
            self._checkpoint("create_telegram_deliveries")

            review_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(reviews)")
            }
            if "retrieval_model" not in review_columns:
                connection.execute(
                    "ALTER TABLE reviews ADD COLUMN retrieval_model TEXT NULL"
                )
                self._checkpoint("add_retrieval_model")
            if "retrieval_recipe" not in review_columns:
                connection.execute(
                    "ALTER TABLE reviews ADD COLUMN retrieval_recipe TEXT NULL"
                )
                self._checkpoint("add_retrieval_recipe")

            job_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(generation_jobs)")
            }
            if "attempt_count" not in job_columns:
                connection.execute(
                    """
                    ALTER TABLE generation_jobs
                    ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0
                    """
                )
                self._checkpoint("add_attempt_count")
            if "next_attempt_at" not in job_columns:
                connection.execute(
                    """
                    ALTER TABLE generation_jobs
                    ADD COLUMN next_attempt_at TEXT NULL
                    """
                )
                self._checkpoint("add_next_attempt_at")
            if "last_error" not in job_columns:
                connection.execute(
                    "ALTER TABLE generation_jobs ADD COLUMN last_error TEXT NULL"
                )
                self._checkpoint("add_last_error")

            connection.execute("DROP TABLE IF EXISTS corpus_embeddings")
            self._checkpoint("drop_corpus_embeddings")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._checkpoint("create_review_metadata")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_audit (
                    kind TEXT NOT NULL,
                    period TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (kind, period),
                    FOREIGN KEY (kind, period) REFERENCES reviews(kind, period)
                        ON DELETE CASCADE
                )
                """
            )
            self._checkpoint("create_review_audit")
            connection.execute(f"PRAGMA user_version = {REVIEWS_SCHEMA_VERSION}")
            self._checkpoint("set_user_version")

    def upsert_review(
        self,
        record: ReviewRecord,
        *,
        sources: Sequence[ReviewSource],
    ) -> None:
        with self._connection() as connection:
            previous_row = connection.execute(
                "SELECT * FROM reviews WHERE kind = ? AND period = ?",
                (record.kind, record.period),
            ).fetchone()
            dependency_changed = (
                previous_row is not None
                and _review_dependency_fingerprint(_review_from_row(previous_row))
                != _review_dependency_fingerprint(record)
            )
            connection.execute(
                """
                INSERT INTO reviews (
                    kind, period, start_date, end_date, status, title, payload,
                    telegram_caption, reflection_question, safety_note,
                    image_path, image_alt, language, model, source_hash,
                    retrieval_model, retrieval_recipe
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, period) DO UPDATE SET
                    start_date=excluded.start_date, end_date=excluded.end_date,
                    status=excluded.status, title=excluded.title,
                    payload=excluded.payload, telegram_caption=excluded.telegram_caption,
                    reflection_question=excluded.reflection_question,
                    safety_note=excluded.safety_note, image_path=excluded.image_path,
                    image_alt=excluded.image_alt, language=excluded.language,
                    model=excluded.model, source_hash=excluded.source_hash,
                    retrieval_model=excluded.retrieval_model,
                    retrieval_recipe=excluded.retrieval_recipe
                """,
                _review_values(record),
            )
            connection.execute(
                "DELETE FROM review_sources WHERE kind = ? AND period = ?",
                (record.kind, record.period),
            )
            connection.executemany(
                """
                INSERT INTO review_sources
                    (kind, period, source_id, source_type, source_hash, label, position)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.kind,
                        record.period,
                        source.source_id,
                        source.source_type,
                        source.source_hash,
                        source.label,
                        source.position,
                    )
                    for source in sources
                ],
            )
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                INSERT INTO review_audit (kind, period, version, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(kind, period) DO UPDATE SET
                    version=review_audit.version + 1,
                    updated_at=excluded.updated_at
                """,
                (record.kind, record.period, now, now),
            )
            if dependency_changed:
                dependency_id = f"review:{record.kind}:{record.period}"
                dependent_rows = connection.execute(
                    """
                    SELECT DISTINCT kind, period FROM review_sources
                    WHERE source_id = ? AND NOT (kind = ? AND period = ?)
                    """,
                    (dependency_id, record.kind, record.period),
                ).fetchall()
                connection.executemany(
                    "UPDATE reviews SET status = 'stale' WHERE kind = ? AND period = ?",
                    [
                        (row["kind"], row["period"])
                        for row in dependent_rows
                    ],
                )

    def get_review(self, kind: str, period: str) -> ReviewRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM reviews WHERE kind = ? AND period = ?", (kind, period)
            ).fetchone()
        return _review_from_row(row) if row is not None else None

    def list_reviews(self) -> list[ReviewRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reviews
                WHERE status = 'ready'
                ORDER BY end_date, kind, period
                """
            ).fetchall()
        return [_review_from_row(row) for row in rows]

    def list_review_sources(self, kind: str, period: str) -> list[ReviewSource]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT source_id, source_type, source_hash, label, position
                FROM review_sources WHERE kind = ? AND period = ? ORDER BY position
                """,
                (kind, period),
            ).fetchall()
        return [ReviewSource(**dict(row)) for row in rows]

    def enqueue_job(
        self, kind: str, period: str, source_hash: str, *, reason: str
    ) -> GenerationJob:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO generation_jobs
                    (kind, period, source_hash, reason, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (kind, period, source_hash, reason),
            )
            row = connection.execute(
                """
                SELECT * FROM generation_jobs
                WHERE kind = ? AND period = ? AND source_hash = ? AND reason = ?
                """,
                (kind, period, source_hash, reason),
            ).fetchone()
            if (
                row is not None
                and reason == "stale"
                and row["status"] in {"complete", "superseded"}
            ):
                connection.execute(
                    """
                    UPDATE generation_jobs
                    SET status = 'pending', attempt_count = 0,
                        next_attempt_at = NULL, last_error = NULL
                    WHERE job_id = ?
                    """,
                    (row["job_id"],),
                )
                row = connection.execute(
                    "SELECT * FROM generation_jobs WHERE job_id = ?",
                    (row["job_id"],),
                ).fetchone()
        assert row is not None
        return _job_from_row(row)

    def enqueue_regeneration(
        self, kind: str, period: str, source_hash: str
    ) -> GenerationJob:
        with self._connection() as connection:
            active = connection.execute(
                """
                SELECT * FROM generation_jobs
                WHERE kind = ? AND period = ?
                  AND reason LIKE 'regenerate:%'
                  AND status IN ('pending', 'running')
                ORDER BY job_id DESC LIMIT 1
                """,
                (kind, period),
            ).fetchone()
            if active is not None:
                return _job_from_row(active)
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            cursor = connection.execute(
                """
                INSERT INTO generation_jobs
                    (kind, period, source_hash, reason, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (kind, period, source_hash, f"regenerate:{suffix}"),
            )
            row = connection.execute(
                "SELECT * FROM generation_jobs WHERE job_id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        assert row is not None
        return _job_from_row(row)

    def enqueue_recipe_migrations(
        self,
        *,
        model: str,
        recipe: str,
    ) -> list[GenerationJob]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reviews = connection.execute(
                """
                SELECT kind, period, source_hash FROM reviews
                WHERE retrieval_model IS NULL OR retrieval_model != ?
                   OR retrieval_recipe IS NULL OR retrieval_recipe != ?
                ORDER BY end_date,
                    CASE kind WHEN 'week' THEN 0 ELSE 1 END,
                    period
                """,
                (model, recipe),
            ).fetchall()
            connection.executemany(
                """
                INSERT OR IGNORE INTO generation_jobs
                    (kind, period, source_hash, reason, status)
                VALUES (?, ?, ?, 'recipe_migration', 'pending')
                """,
                [
                    (row["kind"], row["period"], row["source_hash"])
                    for row in reviews
                ],
            )
            rows = connection.execute(
                """
                SELECT jobs.* FROM generation_jobs AS jobs
                JOIN reviews
                  ON reviews.kind = jobs.kind AND reviews.period = jobs.period
                WHERE jobs.reason = 'recipe_migration'
                  AND (reviews.retrieval_model IS NULL
                       OR reviews.retrieval_model != ?
                       OR reviews.retrieval_recipe IS NULL
                       OR reviews.retrieval_recipe != ?)
                ORDER BY reviews.end_date,
                    CASE reviews.kind WHEN 'week' THEN 0 ELSE 1 END,
                    reviews.period
                """,
                (model, recipe),
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def get_job(self, job_id: int) -> GenerationJob | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM generation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return None if row is None else _job_from_row(row)

    def claim_next_job(
        self,
        *,
        now: datetime | None = None,
    ) -> GenerationJob | None:
        current = _as_utc(now or datetime.now(timezone.utc)).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM generation_jobs
                WHERE status = 'pending'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY CASE
                    WHEN reason = 'scheduled' THEN 0
                    WHEN reason LIKE 'regenerate:%' OR reason = 'stale' THEN 1
                    WHEN reason = 'recipe_migration' THEN 2
                    WHEN reason = 'backfill' THEN 3
                    ELSE 4
                END, job_id
                LIMIT 1
                """,
                (current,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE generation_jobs SET status = 'running' WHERE job_id = ?",
                (row["job_id"],),
            )
            result = dict(row)
            result["status"] = "running"
        return _job_from_mapping(result)

    def set_job_status(self, job_id: int, status: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE generation_jobs SET status = ? WHERE job_id = ?",
                (status, job_id),
            )

    def record_job_failure(
        self,
        job_id: int,
        error: BaseException,
        *,
        now: datetime | None = None,
    ) -> GenerationJob:
        current = _as_utc(now or datetime.now(timezone.utc))
        delays = (
            timedelta(minutes=1),
            timedelta(minutes=5),
            timedelta(minutes=30),
            timedelta(hours=2),
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM generation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Review generation job is missing")
            attempt_count = int(row["attempt_count"]) + 1
            terminal = attempt_count >= 5
            next_attempt_at = (
                None
                if terminal
                else (current + delays[attempt_count - 1]).isoformat()
            )
            connection.execute(
                """
                UPDATE generation_jobs
                SET status = ?, attempt_count = ?, next_attempt_at = ?,
                    last_error = ?
                WHERE job_id = ?
                """,
                (
                    "failed" if terminal else "pending",
                    attempt_count,
                    next_attempt_at,
                    str(error),
                    job_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM generation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        assert updated is not None
        return _job_from_row(updated)

    def reset_running_jobs(self) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE generation_jobs SET status = 'pending' WHERE status = 'running'"
            )
            return cursor.rowcount

    def invalidate_source(self, source_id: str, new_hash: str) -> list[tuple[str, str]]:
        del new_hash
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT kind, period FROM review_sources WHERE source_id = ?",
                (source_id,),
            ).fetchall()
            affected = sorted((row["kind"], row["period"]) for row in rows)
            connection.executemany(
                "UPDATE reviews SET status = 'stale' WHERE kind = ? AND period = ?",
                affected,
            )
        return affected

    def record_delivery(self, kind: str, period: str, *, chat_id: int, status: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO telegram_deliveries (kind, period, chat_id, status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(kind, period, chat_id) DO UPDATE SET status=excluded.status
                """,
                (kind, period, chat_id, status),
            )

    def get_delivery(
        self, kind: str, period: str, chat_id: int
    ) -> TelegramDelivery | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT kind, period, chat_id, status FROM telegram_deliveries
                WHERE kind = ? AND period = ? AND chat_id = ?
                """,
                (kind, period, chat_id),
            ).fetchone()
        return TelegramDelivery(**dict(row)) if row is not None else None

    def replace_corpus(self, documents: Sequence[CorpusDocument]) -> None:
        with self._connection() as connection:
            document_ids = [document.document_id for document in documents]
            if document_ids:
                placeholders = ",".join("?" for _ in document_ids)
                connection.execute(
                    f"DELETE FROM corpus_documents WHERE document_id NOT IN ({placeholders})",
                    document_ids,
                )
            else:
                connection.execute("DELETE FROM corpus_documents")
            connection.executemany(
                """
                INSERT INTO corpus_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    source_type=excluded.source_type,
                    path=excluded.path,
                    heading=excluded.heading,
                    text=excluded.text,
                    content_hash=excluded.content_hash,
                    document_date=excluded.document_date,
                    first_seen=excluded.first_seen
                """,
                [
                    (
                        document.document_id,
                        document.source_type,
                        document.path,
                        document.heading,
                        document.text,
                        document.content_hash,
                        document.document_date.isoformat()
                        if document.document_date
                        else None,
                        document.first_seen.isoformat(),
                    )
                    for document in documents
                ],
            )

    def list_corpus_documents(self) -> list[CorpusDocument]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM corpus_documents ORDER BY document_id"
            ).fetchall()
        return [
            CorpusDocument(
                document_id=row["document_id"],
                source_type=row["source_type"],
                path=row["path"],
                heading=row["heading"],
                text=row["text"],
                content_hash=row["content_hash"],
                document_date=date.fromisoformat(row["document_date"])
                if row["document_date"]
                else None,
                first_seen=datetime.fromisoformat(row["first_seen"]),
            )
            for row in rows
        ]

    def upsert_embedding(
        self,
        document_id: str,
        embedding: Sequence[float],
        *,
        embedding_model: str,
        content_hash: str,
    ) -> None:
        self.embeddings.upsert_embedding(
            document_id=document_id,
            content_hash=content_hash,
            model=embedding_model,
            recipe_version=recipe_for_model(embedding_model),
            embedding=embedding,
        )

    def list_embedded_documents(self) -> list[EmbeddedDocument]:
        documents = {
            document.document_id: document for document in self.list_corpus_documents()
        }
        rows = self.embeddings.list_embeddings()
        return [
            EmbeddedDocument(
                document=documents[row.document_id],
                embedding=row.embedding,
                embedding_model=row.model,
                embedding_dimension=row.dimension,
                content_hash=row.content_hash,
            )
            for row in rows
            if row.document_id in documents
        ]

    def list_jobs(self) -> list[GenerationJob]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM generation_jobs ORDER BY job_id"
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def list_deliveries(self) -> list[TelegramDelivery]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT kind, period, chat_id, status FROM telegram_deliveries
                ORDER BY kind, period, chat_id
                """
            ).fetchall()
        return [TelegramDelivery(**dict(row)) for row in rows]

    def get_metadata(self, key: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM review_metadata WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row is not None else None

    def set_metadata(self, key: str, value: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO review_metadata (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )


def _review_values(record: ReviewRecord) -> tuple[object, ...]:
    return (
        record.kind,
        record.period,
        record.start_date.isoformat(),
        record.end_date.isoformat(),
        record.status,
        record.title,
        json.dumps(record.payload, ensure_ascii=False, sort_keys=True),
        record.telegram_caption,
        record.reflection_question,
        record.safety_note,
        record.image_path,
        record.image_alt,
        record.language,
        record.model,
        record.source_hash,
        record.retrieval_model,
        record.retrieval_recipe,
    )


def _review_dependency_fingerprint(record: ReviewRecord) -> str:
    payload = {
        "title": record.title,
        "payload": record.payload,
        "reflection_question": record.reflection_question,
        "safety_note": record.safety_note,
        "source_hash": record.source_hash,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _review_from_row(row: sqlite3.Row) -> ReviewRecord:
    return ReviewRecord(
        kind=row["kind"],
        period=row["period"],
        start_date=date.fromisoformat(row["start_date"]),
        end_date=date.fromisoformat(row["end_date"]),
        status=row["status"],
        title=row["title"],
        payload=json.loads(row["payload"]),
        telegram_caption=row["telegram_caption"],
        reflection_question=row["reflection_question"],
        safety_note=row["safety_note"],
        image_path=row["image_path"],
        image_alt=row["image_alt"],
        language=row["language"],
        model=row["model"],
        source_hash=row["source_hash"],
        retrieval_model=row["retrieval_model"],
        retrieval_recipe=row["retrieval_recipe"],
    )


def _job_from_row(row: sqlite3.Row) -> GenerationJob:
    return _job_from_mapping(dict(row))


def _job_from_mapping(row: dict[str, object]) -> GenerationJob:
    return GenerationJob(
        job_id=int(row["job_id"]),
        kind=str(row["kind"]),
        period=str(row["period"]),
        source_hash=str(row["source_hash"]),
        reason=str(row["reason"]),
        status=str(row["status"]),
        attempt_count=int(row.get("attempt_count") or 0),
        next_attempt_at=(
            None
            if row.get("next_attempt_at") is None
            else datetime.fromisoformat(str(row["next_attempt_at"]))
        ),
        last_error=(
            None if row.get("last_error") is None else str(row["last_error"])
        ),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
