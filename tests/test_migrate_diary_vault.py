import asyncio
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from dairy_bot.services.enrichment_db import EnrichmentStore
from dairy_bot.services.enrichment_schemas import DayEnrichment, Mood, NoteEnrichment, Topic
from scripts.migrate_diary_vault import (
    MigrationConfig,
    manual_overrides_from_frontmatter,
    run_migration,
    scan_notes,
)


TZ = ZoneInfo("Europe/Vienna")


class FakeMigrationClient:
    def __init__(self):
        self.note_calls: list[str] = []
        self.day_calls: list[str] = []

    async def enrich_note(self, text: str) -> NoteEnrichment:
        self.note_calls.append(text)
        return NoteEnrichment(
            gist=f"Gist {len(self.note_calls)}",
            mood_evidence="Fake note mood evidence.",
            mood=Mood.calm,
            mood_confidence=0.71,
            topics=[Topic.reflection],
        )

    async def embed_note(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    async def enrich_day(self, text: str) -> DayEnrichment:
        self.day_calls.append(text)
        return DayEnrichment(
            summary="Fake day summary.",
            mood=Mood.neutral,
            mood_confidence=0.42,
            key_topics=[Topic.reflection],
            sport_evidence="LLM sport evidence.",
            sport=True,
            reading_evidence=None,
            reading=None,
            purchases_evidence="LLM purchases evidence.",
            purchases=True,
            eating_outside_evidence=None,
            eating_outside=None,
            deep_focus_evidence=None,
            deep_focus=None,
            sleep_quality_evidence=None,
            sleep_quality=None,
        )


class TrackingMigrationClient(FakeMigrationClient):
    def __init__(self):
        super().__init__()
        self.active_note_calls = 0
        self.max_active_note_calls = 0
        self.active_day_calls = 0
        self.max_active_day_calls = 0

    async def enrich_note(self, text: str) -> NoteEnrichment:
        self.active_note_calls += 1
        self.max_active_note_calls = max(
            self.max_active_note_calls,
            self.active_note_calls,
        )
        await asyncio.sleep(0.01)
        try:
            return await super().enrich_note(text)
        finally:
            self.active_note_calls -= 1

    async def enrich_day(self, text: str) -> DayEnrichment:
        self.active_day_calls += 1
        self.max_active_day_calls = max(
            self.max_active_day_calls,
            self.active_day_calls,
        )
        await asyncio.sleep(0.01)
        try:
            return await super().enrich_day(text)
        finally:
            self.active_day_calls -= 1


def run(coro):
    return asyncio.run(coro)


def note_path(root: Path, day: str) -> Path:
    year, month, _ = day.split("-")
    return root / year / month / f"{day}.md"


def write_note(root: Path, day: str, text: str) -> Path:
    path = note_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def frontmatter(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8").split("---", 2)[1]
    return yaml.safe_load(raw)


def test_scan_classifies_empty_current_and_legacy_section_notes(tmp_path):
    write_note(
        tmp_path,
        "2026-06-17",
        """---
mood_morning: null
sport: null
habits:
  reading: null
deep_questions_asked:
- A generated prompt
---
# 2026-06-17
[[2026-06-16|Prev day]]
""",
    )
    write_note(
        tmp_path,
        "2026-06-18",
        """---
mood_morning: 3
sport: true
---
# 2026-06-18
[[2026-06-17|Prev day]]

## 20:50

Current style body.
""",
    )
    write_note(
        tmp_path,
        "2025-03-10",
        """*10-03-2025 | 09-52*
[[2025-03-04|17-28 февраля 2025]]

---

## Что Сделано?

- Показал версию LLM-агента.
""",
    )

    notes = {note.date.isoformat(): note for note in scan_notes(tmp_path, show_progress=False)}

    assert notes["2026-06-17"].contentful is False
    assert notes["2026-06-18"].contentful is True
    assert notes["2026-06-18"].entries[0].timestamp == "20:50"
    assert notes["2026-06-18"].entries[0].kind == "text"
    assert notes["2025-03-10"].contentful is True
    assert notes["2025-03-10"].entries[0].timestamp == "09:52"
    assert "Что Сделано" in notes["2025-03-10"].entries[0].text
    assert "17-28 февраля" not in notes["2025-03-10"].entries[0].text


def test_manual_overrides_map_only_current_day_facts():
    overrides = manual_overrides_from_frontmatter(
        {
            "mood_morning": 1,
            "energy": 5,
            "anxiety": 4,
            "cravings": 3,
            "sport": False,
            "sleep_score": 80,
            "focus": 5,
            "weather": {"city": "Vienna"},
            "habits": {
                "reading": True,
                "no_eating_out": False,
                "zero_spending": True,
                "steps_8k": True,
                "supplements": True,
            },
        }
    )

    assert overrides.facts == {
        "sport": False,
        "reading": True,
        "eating_outside": True,
        "purchases": False,
        "sleep_quality": 4,
        "deep_focus": True,
    }
    assert "mood_morning" not in overrides.facts
    assert "steps_8k" not in overrides.facts
    assert all(value.startswith("Manual metadata:") for value in overrides.evidence.values())


def test_manual_overrides_sleep_duration_fallback_and_low_focus():
    overrides = manual_overrides_from_frontmatter(
        {
            "sleep_duration": 375,
            "focus": 2,
            "habits": {"no_eating_out": True, "zero_spending": False},
        }
    )

    assert overrides.facts["sleep_quality"] == 3
    assert overrides.facts["deep_focus"] is False
    assert overrides.facts["eating_outside"] is False
    assert overrides.facts["purchases"] is True


def test_dry_run_makes_no_file_or_db_changes(tmp_path):
    content_path = write_note(
        tmp_path,
        "2026-01-01",
        """# 2026-01-01

## 10:00

Keep me.
""",
    )
    empty_path = write_note(
        tmp_path,
        "2026-01-02",
        """---
sport: true
---
# 2026-01-02
[[2026-01-01|Prev day]]
""",
    )
    db_path = tmp_path / "data" / "enrichment.sqlite3"
    db_path.parent.mkdir()
    db_path.write_text("sentinel", encoding="utf-8")

    stats = run(
        run_migration(
            MigrationConfig(tmp_path, db_path, timezone=TZ),
            apply=False,
            show_progress=False,
        )
    )

    assert stats.total_files == 2
    assert stats.deleted_files == 1
    assert stats.rewritten_files == 1
    assert stats.note_entries == 1
    assert content_path.read_text(encoding="utf-8").endswith("Keep me.\n")
    assert empty_path.exists()
    assert db_path.read_text(encoding="utf-8") == "sentinel"


def test_apply_deletes_rewrites_enriches_and_applies_manual_overrides(tmp_path):
    content_path = write_note(
        tmp_path,
        "2026-01-01",
        """---
mood_morning: 3
sport: false
sleep_score: 80
focus: 5
habits:
  reading: true
  no_eating_out: false
  zero_spending: true
  steps_8k: true
---
# 2026-01-01
[[2025-03-10|Prev day]] · [[2026-01-02|Next day]]

## 20:50

У меня сегодня был первый созвон.
""",
    )
    empty_path = write_note(
        tmp_path,
        "2026-01-02",
        """---
sport: true
habits:
  reading: true
---
# 2026-01-02
[[2026-01-01|Prev day]]
""",
    )
    legacy_path = write_note(
        tmp_path,
        "2025-03-10",
        """*10-03-2025 | 09-52*
[[2025-03-04|17-28 февраля 2025]]

---

## Что Сделано?

- Показал версию LLM-агента.
""",
    )
    db_path = tmp_path / "data" / "enrichment.sqlite3"
    client = FakeMigrationClient()

    stats = run(
        run_migration(
            MigrationConfig(tmp_path, db_path, timezone=TZ),
            apply=True,
            allow_dirty=True,
            client=client,
            show_progress=False,
        )
    )

    assert stats.deleted_files == 1
    assert stats.rewritten_files == 2
    assert stats.note_entries == 2
    assert stats.day_enrichments == 2
    assert not empty_path.exists()

    content = content_path.read_text(encoding="utf-8")
    assert "mood_morning" not in content
    assert "steps_8k" not in content
    assert "## 20:50 — text" in content
    assert "<!-- dairy:note-enrichment -->" in content
    assert "mood:: calm · topics:: reflection" in content
    assert "[[2025-03-10|Prev day]]" in content
    assert "[[2026-01-02|Next day]]" not in content

    legacy = legacy_path.read_text(encoding="utf-8")
    assert "## 09:52 — text" in legacy
    assert "17-28 февраля" not in legacy

    data = frontmatter(content_path)
    assert data["date"].isoformat() == "2026-01-01"
    assert data["type"] == "daily"
    assert data["sport"] is False
    assert data["reading"] is True
    assert data["eating_outside"] is True
    assert data["purchases"] is False
    assert data["sleep_quality"] == 4
    assert data["deep_focus"] is True

    store = EnrichmentStore(db_path)
    rows = store.list_notes()
    assert [row["id"] for row in rows] == ["2025-03-10T09:52", "2026-01-01T20:50"]
    assert "embedding" not in rows[0]

    day = store.get_day("2026-01-01")
    assert day is not None
    assert day["sport"] == 0
    assert day["reading"] == 1
    assert day["eating_outside"] == 1
    assert day["purchases"] == 0
    assert day["sleep_quality"] == 4
    assert day["deep_focus"] == 1
    assert day["sport_evidence"].startswith("Manual metadata:")


def test_apply_processes_enrichment_with_bounded_concurrency(tmp_path):
    for day in ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"):
        write_note(
            tmp_path,
            day,
            f"""# {day}

## 10:00

Entry for {day}.
""",
        )
    db_path = tmp_path / "data" / "enrichment.sqlite3"
    client = TrackingMigrationClient()

    run(
        run_migration(
            MigrationConfig(tmp_path, db_path, timezone=TZ),
            apply=True,
            allow_dirty=True,
            client=client,
            note_concurrency=2,
            day_concurrency=2,
            show_progress=False,
        )
    )

    assert client.max_active_note_calls == 2
    assert client.max_active_day_calls == 2
    assert len(client.note_calls) == 4
    assert len(client.day_calls) == 4
