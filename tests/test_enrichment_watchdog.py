import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import bot as bot_module
from dairy_bot.services import storage
from dairy_bot.services.enrichment_schemas import DayEnrichment, Mood, NoteEnrichment, Topic


TZ = ZoneInfo("Europe/Vienna")


class FakeSettings:
    def __init__(self, journal_dir: Path):
        self.journal_dir = journal_dir
        self.timezone = TZ
        self.toc_enabled = True
        self.toc_filename = "table_of_contents.md"
        self.toc_extra_dirs = []
        self.toc_model = "test/model"
        self.toc_max_tags = 5
        self.enrichment_enabled = True
        self.enrichment_db_path = journal_dir / "data" / "enrichment.sqlite3"


class FakeGit:
    def __init__(self):
        self.committed_paths: list[Path] | None = None

    def prepare_for_write(self):
        return None

    def commit_and_push(self, paths):
        self.committed_paths = list(paths)
        return SimpleNamespace(pushed=True)


class FakeClient:
    def __init__(self):
        self.note_calls = 0
        self.day_calls = 0

    async def enrich_note(self, text: str) -> NoteEnrichment:
        self.note_calls += 1
        return NoteEnrichment(
            gist="A changed note.",
            mood_evidence="The note sounds sad.",
            mood=Mood.sadness,
            mood_confidence=0.64,
            topics=[Topic.reflection],
        )

    async def embed_note(self, text: str) -> list[float]:
        return [0.9]

    async def enrich_day(self, text: str) -> DayEnrichment:
        self.day_calls += 1
        return DayEnrichment(
            summary="A reflective changed day.",
            mood=Mood.sadness,
            mood_confidence=0.64,
            key_topics=[Topic.reflection],
        )


def run(coro):
    return asyncio.run(coro)


def note_path(root: Path, day: str) -> Path:
    year, month, _ = day.split("-")
    return root / year / month / f"{day}.md"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_AC_1_watchdog_enriches_changed_non_today_notes_before_toc(tmp_path, monkeypatch):
    past_path = run(
        storage.append_entry(
            tmp_path,
            "Manual old-day text",
            moment=datetime(2026, 6, 13, 9, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )
    settings = FakeSettings(tmp_path)
    git = FakeGit()
    client = FakeClient()
    toc_seen: list[str] = []

    async def fake_reconcile_toc(journal_dir, settings, target_paths=None):
        toc_seen.append(read_text(past_path))
        toc_path = journal_dir / "table_of_contents.md"
        toc_path.write_text("# TOC\n", encoding="utf-8")
        return [toc_path]

    monkeypatch.setattr(bot_module, "build_enrichment_client", lambda settings: client)
    monkeypatch.setattr(bot_module, "reconcile_toc", fake_reconcile_toc)

    run(
        bot_module._reconcile_background_once(
            settings,
            git,
            "Test",
            now=datetime(2026, 6, 16, 12, 0, tzinfo=TZ),
        )
    )

    assert "mood:: sadness · topics:: reflection" in toc_seen[0]
    assert "summary: A reflective changed day." in read_text(past_path)
    assert client.note_calls == 1
    assert client.day_calls == 1
    assert git.committed_paths and past_path in git.committed_paths


def test_AC_2_watchdog_skips_unchanged_notes_without_llm_calls(tmp_path, monkeypatch):
    run(
        storage.append_entry(
            tmp_path,
            "Already indexed old-day text",
            moment=datetime(2026, 6, 13, 9, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )
    settings = FakeSettings(tmp_path)
    git = FakeGit()
    client = FakeClient()

    async def fake_reconcile_toc(journal_dir, settings, target_paths=None):
        return []

    monkeypatch.setattr(bot_module, "build_enrichment_client", lambda settings: client)
    monkeypatch.setattr(bot_module, "reconcile_toc", fake_reconcile_toc)

    run(
        bot_module._reconcile_background_once(
            settings,
            git,
            "First",
            now=datetime(2026, 6, 16, 12, 0, tzinfo=TZ),
        )
    )
    run(
        bot_module._reconcile_background_once(
            settings,
            git,
            "Second",
            now=datetime(2026, 6, 16, 12, 0, tzinfo=TZ),
        )
    )

    assert client.note_calls == 1
    assert client.day_calls == 1


def _setup(tmp_path, monkeypatch):
    settings = FakeSettings(tmp_path)
    git = FakeGit()
    client = FakeClient()

    async def fake_reconcile_toc(journal_dir, settings, target_paths=None):
        return []

    monkeypatch.setattr(bot_module, "build_enrichment_client", lambda settings: client)
    monkeypatch.setattr(bot_module, "reconcile_toc", fake_reconcile_toc)
    return settings, git, client


def _reconcile(settings, git):
    run(
        bot_module._reconcile_background_once(
            settings,
            git,
            "Test",
            now=datetime(2026, 6, 16, 12, 0, tzinfo=TZ),
        )
    )


def test_AC_3_summary_rewrite_alone_does_not_retrigger_enrichment(
    tmp_path, monkeypatch
):
    path = run(
        storage.append_entry(
            tmp_path,
            "Old-day text",
            moment=datetime(2026, 6, 13, 9, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )
    settings, git, client = _setup(tmp_path, monkeypatch)

    _reconcile(settings, git)
    rewritten = read_text(path).replace(
        "A reflective changed day.", "A differently phrased day."
    )
    path.write_text(rewritten, encoding="utf-8")
    git.committed_paths = None

    _reconcile(settings, git)

    assert client.note_calls == 1
    assert client.day_calls == 1
    assert git.committed_paths is None


def test_AC_4_manual_entry_edit_retriggers_note_and_day_enrichment(
    tmp_path, monkeypatch
):
    path = run(
        storage.append_entry(
            tmp_path,
            "Original entry text",
            moment=datetime(2026, 6, 13, 9, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )
    settings, git, client = _setup(tmp_path, monkeypatch)

    _reconcile(settings, git)
    edited = read_text(path).replace("Original entry text", "Edited entry text")
    path.write_text(edited, encoding="utf-8")

    _reconcile(settings, git)

    assert client.note_calls == 2
    assert client.day_calls == 2


def test_AC_5_entry_deletion_retriggers_day_enrichment_only(tmp_path, monkeypatch):
    run(
        storage.append_entry(
            tmp_path,
            "First entry",
            moment=datetime(2026, 6, 13, 9, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )
    path = run(
        storage.append_entry(
            tmp_path,
            "Second entry",
            moment=datetime(2026, 6, 13, 10, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )
    settings, git, client = _setup(tmp_path, monkeypatch)

    _reconcile(settings, git)
    content = read_text(path)
    truncated = content[: content.index("## 10:00")].rstrip() + "\n"
    path.write_text(truncated, encoding="utf-8")

    _reconcile(settings, git)

    assert client.note_calls == 2
    assert client.day_calls == 2


def test_AC_6_missing_watchdog_state_does_not_reenrich_existing_vault(
    tmp_path, monkeypatch
):
    run(
        storage.append_entry(
            tmp_path,
            "Already enriched text",
            moment=datetime(2026, 6, 13, 9, 0, tzinfo=TZ),
            timezone=TZ,
        )
    )
    settings, git, client = _setup(tmp_path, monkeypatch)

    _reconcile(settings, git)
    with sqlite3.connect(settings.enrichment_db_path) as conn:
        conn.execute("DELETE FROM file_state")
    git.committed_paths = None

    _reconcile(settings, git)

    assert client.note_calls == 1
    assert client.day_calls == 1
    assert git.committed_paths is None
