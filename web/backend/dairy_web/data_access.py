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


class EnrichmentReadStore:
    """Read-only adapter for the bot-owned enrichment SQLite database."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._read_only_uri(), uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def list_notes(self) -> list[NoteRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM notes ORDER BY id").fetchall()
        return [_note_from_row(row) for row in rows]

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
        return None if row is None else _note_from_row(row)

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


def _note_from_row(row: sqlite3.Row) -> NoteRecord:
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
        embedding=[float(value) for value in json.loads(row["embedding"])],
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
