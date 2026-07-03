from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from dairy_bot.services.enrichment_schemas import DayEnrichment, NoteEnrichment


DAY_COLUMNS = (
    "date",
    "summary",
    "mood",
    "mood_confidence",
    "key_topics_json",
    "sport",
    "sport_evidence",
    "reading",
    "reading_evidence",
    "purchases",
    "purchases_evidence",
    "eating_outside",
    "eating_outside_evidence",
    "deep_focus",
    "deep_focus_evidence",
    "sleep_quality",
    "sleep_quality_evidence",
    "weekday",
    "is_weekend",
    "season",
)
DAY_UPDATE_COLUMNS = DAY_COLUMNS[1:]


class EnrichmentStore:
    """Small SQLite cache for enrichment values and change detection state."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS notes (
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

                CREATE TABLE IF NOT EXISTS days (
                    date TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    mood TEXT NOT NULL,
                    mood_confidence REAL NOT NULL,
                    key_topics_json TEXT NOT NULL,
                    sport INTEGER NULL,
                    sport_evidence TEXT NULL,
                    reading INTEGER NULL,
                    reading_evidence TEXT NULL,
                    purchases INTEGER NULL,
                    purchases_evidence TEXT NULL,
                    eating_outside INTEGER NULL,
                    eating_outside_evidence TEXT NULL,
                    deep_focus INTEGER NULL,
                    deep_focus_evidence TEXT NULL,
                    sleep_quality INTEGER NULL,
                    sleep_quality_evidence TEXT NULL,
                    weekday TEXT NOT NULL,
                    is_weekend INTEGER NOT NULL,
                    season TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS note_entry_state (
                    id TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS file_state (
                    note_path TEXT NOT NULL,
                    state_key TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    PRIMARY KEY (note_path, state_key)
                );
                """
            )

    def upsert_note(
        self,
        *,
        note_id: str,
        date: str,
        ts: str,
        note_path: str,
        enrichment: NoteEnrichment,
        embedding: list[float],
        content_hash: str,
    ) -> None:
        topics = [topic.value for topic in enrichment.topics]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notes (
                    id, date, ts, note_path, gist, mood, mood_confidence,
                    topics_json, mood_evidence, embedding
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    date = excluded.date,
                    ts = excluded.ts,
                    note_path = excluded.note_path,
                    gist = excluded.gist,
                    mood = excluded.mood,
                    mood_confidence = excluded.mood_confidence,
                    topics_json = excluded.topics_json,
                    mood_evidence = excluded.mood_evidence,
                    embedding = excluded.embedding
                """,
                (
                    note_id,
                    date,
                    ts,
                    note_path,
                    enrichment.gist,
                    enrichment.mood.value,
                    enrichment.mood_confidence,
                    json.dumps(topics, ensure_ascii=False),
                    enrichment.mood_evidence,
                    json.dumps(embedding),
                ),
            )
            conn.execute(
                """
                INSERT INTO note_entry_state (id, content_hash)
                VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET content_hash = excluded.content_hash
                """,
                (note_id, content_hash),
            )

    def get_note_entry_hash(self, note_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT content_hash FROM note_entry_state WHERE id = ?", (note_id,)
            ).fetchone()
        return None if row is None else str(row["content_hash"])

    def get_note(self, note_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return None if row is None else dict(row)

    def list_notes(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM notes ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def delete_notes_missing_from_path(self, note_path: str, live_ids: set[str]) -> int:
        with self._connect() as conn:
            if live_ids:
                placeholders = ", ".join("?" for _ in live_ids)
                rows = conn.execute(
                    f"""
                    SELECT id FROM notes
                    WHERE note_path = ? AND id NOT IN ({placeholders})
                    """,
                    [note_path, *sorted(live_ids)],
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM notes WHERE note_path = ?",
                    (note_path,),
                ).fetchall()
            stale_ids = [str(row["id"]) for row in rows]
            if not stale_ids:
                return 0
            stale_placeholders = ", ".join("?" for _ in stale_ids)
            conn.execute(
                f"DELETE FROM note_entry_state WHERE id IN ({stale_placeholders})",
                stale_ids,
            )
            conn.execute(
                f"DELETE FROM notes WHERE id IN ({stale_placeholders})",
                stale_ids,
            )
        return len(stale_ids)

    def upsert_day(
        self,
        *,
        date: str,
        enrichment: DayEnrichment,
        weekday: str,
        is_weekend: bool,
        season: str,
    ) -> None:
        payload = _day_payload(
            date=date,
            enrichment=enrichment,
            weekday=weekday,
            is_weekend=is_weekend,
            season=season,
        )
        columns_sql = ", ".join(DAY_COLUMNS)
        placeholders = ", ".join("?" for _ in DAY_COLUMNS)
        updates_sql = ",\n                    ".join(
            f"{column} = excluded.{column}" for column in DAY_UPDATE_COLUMNS
        )
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO days ({columns_sql})
                VALUES ({placeholders})
                ON CONFLICT(date) DO UPDATE SET
                    {updates_sql}
                """,
                [payload[column] for column in DAY_COLUMNS],
            )

    def get_day(self, day: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM days WHERE date = ?", (day,)).fetchone()
        return None if row is None else dict(row)

    def get_file_hash(self, note_path: str, state_key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT content_hash FROM file_state
                WHERE note_path = ? AND state_key = ?
                """,
                (note_path, state_key),
            ).fetchone()
        return None if row is None else str(row["content_hash"])

    def set_file_hash(self, note_path: str, state_key: str, content_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO file_state (note_path, state_key, content_hash)
                VALUES (?, ?, ?)
                ON CONFLICT(note_path, state_key) DO UPDATE SET
                    content_hash = excluded.content_hash
                """,
                (note_path, state_key, content_hash),
            )


def _bool_to_db(value: bool | None) -> int | None:
    if value is None:
        return None
    return int(value)


def _day_payload(
    *,
    date: str,
    enrichment: DayEnrichment,
    weekday: str,
    is_weekend: bool,
    season: str,
) -> dict[str, Any]:
    return {
        "date": date,
        "summary": enrichment.summary,
        "mood": enrichment.mood.value,
        "mood_confidence": enrichment.mood_confidence,
        "key_topics_json": json.dumps(
            [topic.value for topic in enrichment.key_topics],
            ensure_ascii=False,
        ),
        "sport": _bool_to_db(enrichment.sport),
        "sport_evidence": enrichment.sport_evidence,
        "reading": _bool_to_db(enrichment.reading),
        "reading_evidence": enrichment.reading_evidence,
        "purchases": _bool_to_db(enrichment.purchases),
        "purchases_evidence": enrichment.purchases_evidence,
        "eating_outside": _bool_to_db(enrichment.eating_outside),
        "eating_outside_evidence": enrichment.eating_outside_evidence,
        "deep_focus": _bool_to_db(enrichment.deep_focus),
        "deep_focus_evidence": enrichment.deep_focus_evidence,
        "sleep_quality": enrichment.sleep_quality,
        "sleep_quality_evidence": enrichment.sleep_quality_evidence,
        "weekday": weekday,
        "is_weekend": int(is_weekend),
        "season": season,
    }
