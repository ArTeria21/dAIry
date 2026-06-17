import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dairy_bot.services import storage
from dairy_bot.services.enrichment import (
    NoteEnrichmentFailure,
    enrich_daily_note_notes,
    parse_daily_entries,
)
from dairy_bot.services.enrichment_db import EnrichmentStore
from dairy_bot.services.enrichment_schemas import Mood, NoteEnrichment, Topic


TZ = ZoneInfo("Europe/Vienna")


class FakeNoteClient:
    def __init__(self, *, fail_on: str | None = None):
        self.fail_on = fail_on
        self.note_calls: list[str] = []
        self.embedding_calls: list[str] = []

    async def enrich_note(self, text: str) -> NoteEnrichment:
        self.note_calls.append(text)
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("note enrichment failed")
        if "пробежку" in text:
            return NoteEnrichment(
                gist="The user went for a run and felt calmer.",
                mood_evidence="The note says it felt easier after the run.",
                mood=Mood.calm,
                mood_confidence=0.73,
                topics=[Topic.fitness],
            )
        return NoteEnrichment(
            gist="The user felt bad after a German class.",
            mood_evidence="The note says the class was awful and the user felt like an idiot.",
            mood=Mood.anger,
            mood_confidence=0.82,
            topics=[Topic.learning, Topic.identity],
        )

    async def embed_note(self, text: str) -> list[float]:
        self.embedding_calls.append(text)
        return [0.1, 0.2, 0.3]


def run(coro):
    return asyncio.run(coro)


def note_path(root: Path, day: str) -> Path:
    year, month, _ = day.split("-")
    return root / year / month / f"{day}.md"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_AC_1_parse_daily_entries_supports_legacy_and_kind_headings(tmp_path):
    path = note_path(tmp_path, "2026-02-13")
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
date: 2026-02-13
type: daily
---
# 2026-02-13

## 14:32 — voice

Сегодня было ужасное занятие по немецкому.
<!-- dairy:note-enrichment -->
mood:: anger · topics:: learning

## 19:05

Сходил на пробежку, немного отпустило.
""",
        encoding="utf-8",
    )

    entries = parse_daily_entries(read_text(path), path)

    assert [entry.timestamp for entry in entries] == ["14:32", "19:05"]
    assert [entry.kind for entry in entries] == ["voice", "text"]
    assert entries[0].text == "Сегодня было ужасное занятие по немецкому."
    assert entries[1].text == "Сходил на пробежку, немного отпустило."
    assert entries[0].entry_id == "2026-02-13T14:32"
    assert entries[1].entry_id == "2026-02-13T19:05"


def test_AC_2_note_enrichment_attaches_exactly_one_inline_line_and_is_idempotent(tmp_path):
    path = run(
        storage.append_entry(
            tmp_path,
            "Сегодня было ужасное занятие по немецкому, преподавательница меня перебивала.",
            moment=datetime(2026, 2, 13, 14, 32, tzinfo=TZ),
            timezone=TZ,
        )
    )
    store = EnrichmentStore(tmp_path / "data" / "enrichment.sqlite3")
    client = FakeNoteClient()

    changed_first = run(enrich_daily_note_notes(path, tmp_path, client, store))
    changed_second = run(enrich_daily_note_notes(path, tmp_path, client, store))

    content = read_text(path)
    assert changed_first is True
    assert changed_second is False
    assert content.count("mood:: anger · topics:: learning, identity") == 1
    assert "<!-- dairy:note-enrichment -->" in content
    assert "Сегодня было ужасное занятие" in content
    assert len(client.note_calls) == 1


def test_ERR_2_note_enrichment_preserves_user_dataview_mood_topics_fields(tmp_path):
    path = run(
        storage.append_entry(
            tmp_path,
            "Сделал важный рабочий блок.\nmood:: productive · topics:: custom-taxonomy",
            moment=datetime(2026, 2, 13, 14, 32, tzinfo=TZ),
            timezone=TZ,
        )
    )
    store = EnrichmentStore(tmp_path / "data" / "enrichment.sqlite3")

    changed = run(enrich_daily_note_notes(path, tmp_path, FakeNoteClient(), store))

    content = read_text(path)
    assert changed is True
    assert "mood:: productive · topics:: custom-taxonomy" in content
    assert "<!-- dairy:note-enrichment -->" in content
    assert "mood:: anger · topics:: learning, identity" in content
    assert content.index("mood:: productive") < content.index("<!-- dairy:note-enrichment -->")


def test_AC_3_note_enrichment_upserts_sqlite_notes_with_embedding_json(tmp_path):
    path = run(
        storage.append_entry(
            tmp_path,
            "Сходил на пробежку, немного отпустило.",
            moment=datetime(2026, 2, 13, 19, 5, tzinfo=TZ),
            timezone=TZ,
        )
    )
    store = EnrichmentStore(tmp_path / "data" / "enrichment.sqlite3")

    run(enrich_daily_note_notes(path, tmp_path, FakeNoteClient(), store))

    rows = store.list_notes()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "2026-02-13T19:05"
    assert row["date"] == "2026-02-13"
    assert row["ts"] == "19:05"
    assert row["note_path"] == "2026/02/2026-02-13.md"
    assert row["gist"] == "The user went for a run and felt calmer."
    assert row["mood"] == "calm"
    assert row["mood_confidence"] == pytest.approx(0.73)
    assert json.loads(row["topics_json"]) == ["fitness"]
    assert json.loads(row["embedding"]) == [0.1, 0.2, 0.3]


def test_ERR_1_failed_note_enrichment_leaves_raw_note_unchanged_and_retryable(tmp_path):
    path = run(
        storage.append_entry(
            tmp_path,
            "Эту заметку fail надо оставить сырой.",
            moment=datetime(2026, 2, 13, 22, 10, tzinfo=TZ),
            timezone=TZ,
        )
    )
    before = read_text(path)
    store = EnrichmentStore(tmp_path / "data" / "enrichment.sqlite3")

    with pytest.raises(NoteEnrichmentFailure):
        run(enrich_daily_note_notes(path, tmp_path, FakeNoteClient(fail_on="fail"), store))

    assert read_text(path) == before
    assert store.list_notes() == []
