import asyncio
import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from dairy_bot.services.enrichment import enrich_day_summary
from dairy_bot.services.enrichment_db import EnrichmentStore
from dairy_bot.services.enrichment_schemas import DayEnrichment, Mood, Topic


TZ = ZoneInfo("Europe/Vienna")


class FakeDayClient:
    def __init__(self):
        self.calls: list[str] = []

    async def enrich_day(self, text: str) -> DayEnrichment:
        self.calls.append(text)
        return DayEnrichment(
            summary=(
                "A tense day centered on a German lesson, followed by calmer "
                "reflection and a run."
            ),
            mood=Mood.fear,
            mood_confidence=0.62,
            key_topics=[Topic.learning, Topic.identity, Topic.fitness],
            sport_evidence="Сходил на пробежку",
            sport=True,
            reading_evidence=None,
            reading=None,
            purchases_evidence=None,
            purchases=None,
            eating_outside_evidence=None,
            eating_outside=None,
            deep_focus_evidence=None,
            deep_focus=None,
            sleep_quality_evidence=None,
            sleep_quality=None,
        )


def run(coro):
    return asyncio.run(coro)


def note_path(root: Path, day: str) -> Path:
    year, month, _ = day.split("-")
    return root / year / month / f"{day}.md"


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    raw = text.split("---", 2)[1]
    return yaml.safe_load(raw)


def test_AC_N4_day_enrichment_passes_exact_raw_file_to_llm(tmp_path):
    path = note_path(tmp_path, "2026-02-13")
    path.parent.mkdir(parents=True)
    raw_content = """---
date: 2026-02-13
type: daily
---
# 2026-02-13

## 14:32 — voice

Сегодня было ужасное занятие по немецкому.
<!-- dairy:note-enrichment -->
mood:: anger · topics:: learning, identity

## 19:05 — text

Сходил на пробежку, немного отпустило.
<!-- dairy:note-enrichment -->
mood:: calm · topics:: fitness
"""
    path.write_text(raw_content, encoding="utf-8")
    store = EnrichmentStore(tmp_path / "data" / "enrichment.sqlite3")
    client = FakeDayClient()

    changed = run(enrich_day_summary(path, tmp_path, client, store, timezone=TZ))

    data = frontmatter(path)
    assert changed is True
    assert data["date"] == date(2026, 2, 13)
    assert data["type"] == "daily"
    assert data["mood"] == "fear"
    assert data["mood_confidence"] == 0.62
    assert data["key_topics"] == ["learning", "identity", "fitness"]
    assert data["sport"] is True
    assert data["reading"] is None
    assert data["weekday"] == "Friday"
    assert data["is_weekend"] is False
    assert data["season"] == "winter"
    assert "German lesson" in data["summary"]
    assert client.calls == [raw_content]

    row = store.get_day("2026-02-13")
    assert row is not None
    assert row["mood"] == "fear"
    assert json.loads(row["key_topics_json"]) == ["learning", "identity", "fitness"]
    assert row["sport"] == 1
    assert row["reading"] is None
    assert row["weekday"] == "Friday"


def test_EC_1_day_enrichment_preserves_sparse_nulls_in_yaml_and_db(tmp_path):
    path = note_path(tmp_path, "2026-02-13")
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
date: 2026-02-13
type: daily
---
# 2026-02-13

## 14:32

День был тревожным.
""",
        encoding="utf-8",
    )
    store = EnrichmentStore(tmp_path / "data" / "enrichment.sqlite3")

    run(enrich_day_summary(path, tmp_path, FakeDayClient(), store, timezone=TZ))

    data = frontmatter(path)
    row = store.get_day("2026-02-13")
    assert data["purchases"] is None
    assert data["eating_outside"] is None
    assert data["deep_focus"] is None
    assert data["sleep_quality"] is None
    assert row["purchases"] is None
    assert row["eating_outside"] is None
    assert row["deep_focus"] is None
    assert row["sleep_quality"] is None
