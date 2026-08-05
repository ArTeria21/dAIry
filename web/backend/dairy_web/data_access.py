from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import quote


@dataclass(frozen=True, slots=True)
class NoteRecord:
    id: str
    date: str
    ts: str
    note_path: str
    gist: str
    mood: str
    mood_confidence: float
    topics: list[str]
    mood_evidence: str
    embedding: list[float]


@dataclass(frozen=True, slots=True)
class DayRecord:
    date: str
    summary: str
    mood: str
    mood_confidence: float
    key_topics: list[str]
    weekday: str
    is_weekend: bool
    season: str
    facts: dict[str, bool | int | None]


class SemanticIndexBuilding(RuntimeError):
    """The bot has not atomically published the shared semantic index yet."""


class EnrichmentReadStore:
    """Read-only adapter for the bot-owned enrichment SQLite database."""

    def __init__(
        self,
        db_path: Path | str,
        embeddings_db_path: Path | str | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.embeddings_db_path = (
            Path(embeddings_db_path)
            if embeddings_db_path is not None
            else self.db_path.with_name("embeddings.sqlite3")
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._read_only_uri(), uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def list_notes(self) -> list[NoteRecord]:
        _, vectors = self._published_vectors()
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM notes ORDER BY id").fetchall()
        hashes = self.note_content_hashes()
        notes: list[NoteRecord] = []
        for row in rows:
            note_id = str(row["id"])
            vector = vectors.get(f"diary:{note_id}")
            content_hash = hashes.get(note_id)
            if vector is None or content_hash is None or vector[0] != content_hash:
                continue
            notes.append(_note_from_row(row, vector[1]))
        return notes

    def note_content_hashes(self) -> dict[str, str]:
        with self.connect() as conn:
            try:
                rows = conn.execute(
                    "SELECT id, content_hash FROM note_entry_state"
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    return {}
                raise
        return {
            str(row["id"]): "" if row["content_hash"] is None else str(row["content_hash"])
            for row in rows
        }

    def get_note(self, note_id: str) -> NoteRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM notes WHERE id = ?",
                (note_id,),
            ).fetchone()
        if row is None:
            return None
        embedding: list[float] = []
        try:
            _, vectors = self._published_vectors()
            vector = vectors.get(f"diary:{note_id}")
            content_hash = self.note_content_hashes().get(note_id)
            if vector is not None and vector[0] == content_hash:
                embedding = vector[1]
        except SemanticIndexBuilding:
            pass
        return _note_from_row(row, embedding)

    def semantic_signature(self) -> str:
        state = self._semantic_state()
        if state["status"] != "ready":
            raise SemanticIndexBuilding()
        return "|".join(
            str(state[key])
            for key in ("corpus_hash", "model", "recipe_version")
        )

    def list_days(self) -> list[DayRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM days ORDER BY date").fetchall()
        return [_day_from_row(row) for row in rows]

    def get_day(self, day: str) -> DayRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM days WHERE date = ?",
                (day,),
            ).fetchone()
        return None if row is None else _day_from_row(row)

    def _read_only_uri(self) -> str:
        path = quote(str(self.db_path.resolve()), safe="/:")
        return f"file:{path}?mode=ro"

    @contextmanager
    def _semantic_connect(self) -> Iterator[sqlite3.Connection]:
        path = quote(str(self.embeddings_db_path.resolve()), safe="/:")
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.OperationalError as exc:
            raise SemanticIndexBuilding() from exc
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _semantic_state(self) -> dict[str, object]:
        try:
            with self._semantic_connect() as conn:
                conn.execute("BEGIN")
                state = self._active_semantic_state(conn)
        except sqlite3.OperationalError as exc:
            raise SemanticIndexBuilding() from exc
        return state

    def _published_vectors(
        self,
    ) -> tuple[dict[str, object], dict[str, tuple[str, list[float]]]]:
        try:
            with self._semantic_connect() as conn:
                conn.execute("BEGIN")
                state = self._active_semantic_state(conn)
                if state["generation_id"] is None:
                    rows = conn.execute(
                        """
                        SELECT document_id, content_hash, embedding
                        FROM semantic_embeddings
                        WHERE model = ? AND recipe_version = ?
                        """,
                        (state["model"], state["recipe_version"]),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT document_id, content_hash, embedding
                        FROM semantic_vectors
                        WHERE generation_id = ?
                        ORDER BY document_id
                        """,
                        (state["generation_id"],),
                    ).fetchall()
        except sqlite3.OperationalError as exc:
            raise SemanticIndexBuilding() from exc
        vectors = {
            str(row["document_id"]): (
                str(row["content_hash"]),
                [float(value) for value in json.loads(str(row["embedding"]))],
            )
            for row in rows
        }
        return state, vectors

    @staticmethod
    def _active_semantic_state(conn: sqlite3.Connection) -> dict[str, object]:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(semantic_index_state)")
        }
        if "active_generation_id" not in columns:
            row = conn.execute(
                "SELECT * FROM semantic_index_state WHERE singleton = 1"
            ).fetchone()
            if row is None or row["status"] != "ready":
                raise SemanticIndexBuilding()
            return {
                "status": "ready",
                "corpus_hash": str(row["corpus_hash"]),
                "model": str(row["model"]),
                "recipe_version": str(row["recipe_version"]),
                "generation_id": None,
            }

        pointer = conn.execute(
            """
            SELECT active_generation_id
            FROM semantic_index_state WHERE singleton = 1
            """
        ).fetchone()
        if pointer is None or pointer["active_generation_id"] is None:
            raise SemanticIndexBuilding()
        generation = conn.execute(
            """
            SELECT generation_id, status, corpus_hash, model, recipe_version
            FROM semantic_generations WHERE generation_id = ?
            """,
            (pointer["active_generation_id"],),
        ).fetchone()
        if generation is None or generation["status"] != "ready":
            raise SemanticIndexBuilding()
        return {
            "status": "ready",
            "corpus_hash": str(generation["corpus_hash"]),
            "model": str(generation["model"]),
            "recipe_version": str(generation["recipe_version"]),
            "generation_id": int(generation["generation_id"]),
        }


def _note_from_row(row: sqlite3.Row, embedding: list[float]) -> NoteRecord:
    return NoteRecord(
        id=str(row["id"]),
        date=str(row["date"]),
        ts=str(row["ts"]),
        note_path=str(row["note_path"]),
        gist=str(row["gist"]),
        mood=str(row["mood"]),
        mood_confidence=float(row["mood_confidence"]),
        topics=_json_list(row["topics_json"]),
        mood_evidence=str(row["mood_evidence"]),
        embedding=list(embedding),
    )


def _day_from_row(row: sqlite3.Row) -> DayRecord:
    return DayRecord(
        date=str(row["date"]),
        summary=str(row["summary"]),
        mood=str(row["mood"]),
        mood_confidence=float(row["mood_confidence"]),
        key_topics=_json_list(row["key_topics_json"]),
        weekday=str(row["weekday"]),
        is_weekend=bool(row["is_weekend"]),
        season=str(row["season"]),
        facts={
            "sport": _db_bool(row["sport"]),
            "reading": _db_bool(row["reading"]),
            "purchases": _db_bool(row["purchases"]),
            "eating_outside": _db_bool(row["eating_outside"]),
            "deep_focus": _db_bool(row["deep_focus"]),
            "sleep_quality": row["sleep_quality"],
        },
    )


def _json_list(raw: object) -> list[str]:
    value = json.loads(str(raw))
    if not isinstance(value, list):
        raise ValueError("Expected JSON array")
    return [str(item) for item in value]


def _db_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)
