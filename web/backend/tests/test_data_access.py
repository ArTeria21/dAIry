from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dairy_web.data_access import EnrichmentReadStore
from dairy_web.vault_reader import NoteRawTextNotFound, extract_note_raw_text


def create_enrichment_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
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

            CREATE TABLE days (
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
            """
        )
        conn.execute(
            """
            INSERT INTO notes (
                id, date, ts, note_path, gist, mood, mood_confidence,
                topics_json, mood_evidence, embedding
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-06-16T21:55",
                "2026-06-16",
                "21:55",
                "2026/06/2026-06-16.md",
                "The user reflected on language practice.",
                "calm",
                0.82,
                json.dumps(["learning", "reflection"]),
                "The note is reflective and calm.",
                json.dumps([0.1, 0.2, 0.3]),
            ),
        )
        conn.execute(
            """
            INSERT INTO days (
                date, summary, mood, mood_confidence, key_topics_json,
                sport, sport_evidence, reading, reading_evidence,
                purchases, purchases_evidence, eating_outside, eating_outside_evidence,
                deep_focus, deep_focus_evidence, sleep_quality, sleep_quality_evidence,
                weekday, is_weekend, season
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-06-16",
                "A processed summary of the day.",
                "calm",
                0.74,
                json.dumps(["learning"]),
                1,
                "Went running.",
                None,
                None,
                0,
                "No purchases.",
                None,
                None,
                1,
                "Deep work block.",
                4,
                "Slept well.",
                "Tuesday",
                0,
                "summer",
            ),
        )


def test_AC_1_read_store_uses_sqlite_read_only_mode_and_parses_notes_days(tmp_path):
    db_path = tmp_path / "enrichment.sqlite3"
    create_enrichment_db(db_path)
    store = EnrichmentReadStore(db_path)

    notes = store.list_notes()
    days = store.list_days()

    assert [note.id for note in notes] == ["2026-06-16T21:55"]
    assert notes[0].topics == ["learning", "reflection"]
    assert notes[0].embedding == [0.1, 0.2, 0.3]
    assert [day.date for day in days] == ["2026-06-16"]
    assert days[0].facts == {
        "sport": True,
        "reading": None,
        "purchases": False,
        "eating_outside": None,
        "deep_focus": True,
        "sleep_quality": 4,
    }

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO notes (id) VALUES (?)",
                ("2026-06-17T10:00",),
            )


def test_E7_note_content_hashes_degrades_when_state_table_is_absent(tmp_path):
    db_path = tmp_path / "enrichment.sqlite3"
    create_enrichment_db(db_path)
    store = EnrichmentReadStore(db_path)

    assert store.note_content_hashes() == {}


def test_E8_note_content_hashes_reads_available_state_rows_only(tmp_path):
    db_path = tmp_path / "enrichment.sqlite3"
    create_enrichment_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE note_entry_state (
                id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO notes (
                id, date, ts, note_path, gist, mood, mood_confidence,
                topics_json, mood_evidence, embedding
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-06-17T10:15",
                "2026-06-17",
                "10:15",
                "2026/06/2026-06-17.md",
                "A second synthetic note.",
                "calm",
                0.7,
                json.dumps(["learning"]),
                "Synthetic evidence.",
                json.dumps([0.2, 0.3, 0.4]),
            ),
        )
        conn.execute(
            "INSERT INTO note_entry_state (id, content_hash) VALUES (?, ?)",
            ("2026-06-16T21:55", "hash-a"),
        )
    store = EnrichmentReadStore(db_path)

    assert store.note_content_hashes() == {"2026-06-16T21:55": "hash-a"}


def test_AC_2_vault_reader_extracts_matching_entry_without_managed_enrichment(tmp_path):
    note_path = tmp_path / "2026" / "06" / "2026-06-16.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text(
        "\n".join(
            [
                "---",
                "date: 2026-06-16",
                "---",
                "# 2026-06-16",
                "",
                "## June 16 21:55 — voice",
                "",
                "First raw line.",
                "Second raw line.",
                "<!-- dairy:note-enrichment -->",
                "mood:: calm · topics:: learning, reflection",
                "",
                "## 22:20",
                "",
                "Another entry that must not leak.",
            ]
        ),
        encoding="utf-8",
    )

    raw_text = extract_note_raw_text(
        vault_dir=tmp_path,
        note_path="2026/06/2026-06-16.md",
        ts="21:55",
    )

    assert raw_text == "First raw line.\nSecond raw line."


def test_ERR_1_vault_reader_reports_missing_note_without_path_leak(tmp_path):
    with pytest.raises(NoteRawTextNotFound) as exc_info:
        extract_note_raw_text(
            vault_dir=tmp_path,
            note_path="2026/06/2026-06-16.md",
            ts="21:55",
        )

    assert "2026/06/2026-06-16.md" not in str(exc_info.value)
